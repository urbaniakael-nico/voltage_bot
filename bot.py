import logging
import os
import time
import uuid
from datetime import datetime
from typing import Any, Dict, Optional, List

import httpx
from dotenv import load_dotenv
from telegram import ReplyKeyboardMarkup, KeyboardButton, Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

load_dotenv()

TOKEN = os.getenv("TOKEN", "").strip()
API_URL = os.getenv("API_URL", "").strip()
HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "12.0"))
HTTP_CONNECT_TIMEOUT = float(os.getenv("HTTP_CONNECT_TIMEOUT", "3.0"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper().strip()

if not TOKEN:
    raise ValueError("8750941741:AAGDE9toGadffHKN21xROOJ_4Nw6bAUXP4Q)
if not API_URL:
    raise ValueError("❌ https://script.google.com/macros/s/AKfycbytoaZjRotNGaIJschKfL_bvb-sLHMnATo810fGt7YKVk46NFv-MVAaNTX9XXpau1c/exec")

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=getattr(logging, LOG_LEVEL, logging.INFO),
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.INFO)

logger = logging.getLogger("voltage_bot")

UBICACIONES = [
    "Planta principal",
    "Bodega",
    "Obra 1",
    "Obra 2",
    "Obra 3",
]

ESTADOS = {
    "MENU": "menu",
    "UBICACION": "ubicacion",
    "BILLING_NUMERO": "billing_numero",
    "TRABAJANDO": "trabajando",
    "ALMUERZO": "almuerzo",
    "MATERIAL_LISTA": "material_lista",
    "MATERIAL_CANTIDAD": "material_cantidad",
}

USER_CACHE: Dict[str, Dict[str, Any]] = {}
USER_CACHE_TTL = 30
MATERIAL_CACHE: Dict[str, Any] = {"expires_at": 0, "data": []}
MATERIAL_CACHE_TTL = 60


def now_iso() -> str:
    return datetime.now().isoformat()


def get_user_id(update: Update) -> str:
    return str(update.effective_user.id).strip() if update.effective_user else ""


def make_event_id(user_id: str, accion: str) -> str:
    return f"{user_id}-{accion}-{uuid.uuid4().hex[:12]}"


def is_positive_int(texto: str) -> bool:
    return texto.isdigit() and int(texto) > 0


def should_ignore_duplicate(context: ContextTypes.DEFAULT_TYPE, action_key: str, window_seconds: float = 1.3) -> bool:
    now_ts = time.time()
    last_key = context.user_data.get("_last_action_key")
    last_ts = context.user_data.get("_last_action_ts", 0.0)
    if last_key == action_key and (now_ts - last_ts) <= window_seconds:
        return True
    context.user_data["_last_action_key"] = action_key
    context.user_data["_last_action_ts"] = now_ts
    return False


def reset_user_state(context: ContextTypes.DEFAULT_TYPE, user_id: str, nombre: Optional[str] = None, es_lider: Optional[bool] = None) -> None:
    context.user_data.clear()
    context.user_data["estado"] = ESTADOS["MENU"]
    context.user_data["user_id"] = user_id
    if nombre is not None:
        context.user_data["nombre"] = nombre
    if es_lider is not None:
        context.user_data["es_lider"] = es_lider


def menu_principal(es_lider: bool = False) -> ReplyKeyboardMarkup:
    rows = [
        ["🟢 Iniciar turno"],
    ]
    if es_lider:
        rows.append(["📦 Solicitar material"])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def menu_ubicaciones() -> ReplyKeyboardMarkup:
    rows = [[u] for u in UBICACIONES]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def menu_billing() -> ReplyKeyboardMarkup:
    rows = [
        ["Billing 1", "Billing 2", "Billing 3"],
        ["Billing 4", "Billing 5", "Billing 6"],
        ["Billing 7", "Billing 8", "Billing 9"],
    ]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def menu_trabajo(es_lider: bool = False) -> ReplyKeyboardMarkup:
    rows = [["🍽 Salida almuerzo"]]
    if es_lider:
        rows.append(["📦 Solicitar material"])
    rows.append(["🔴 Finalizar jornada"])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def menu_almuerzo(es_lider: bool = False) -> ReplyKeyboardMarkup:
    rows = [["🔁 Regreso almuerzo"]]
    if es_lider:
        rows.append(["📦 Solicitar material"])
    rows.append(["🔴 Finalizar jornada"])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def menu_materiales(materiales: List[Dict[str, Any]]) -> ReplyKeyboardMarkup:
    rows = [[f'{m["codigo"]} - {m["material"]} ({m["stock_actual"]})'] for m in materiales[:40]]
    if not rows:
        rows = [["SIN MATERIALES"]]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


async def post_init(app: Application) -> None:
    timeout = httpx.Timeout(
        timeout=HTTP_TIMEOUT,
        connect=HTTP_CONNECT_TIMEOUT,
        read=HTTP_TIMEOUT,
        write=HTTP_TIMEOUT,
        pool=HTTP_TIMEOUT,
    )
    limits = httpx.Limits(max_connections=50, max_keepalive_connections=20, keepalive_expiry=30.0)
    app.bot_data["http"] = httpx.AsyncClient(
        timeout=timeout,
        limits=limits,
        follow_redirects=True,
        headers={"User-Agent": "voltage-bot/railway-ready"},
    )
    logger.info("HTTP client inicializado")


async def post_shutdown(app: Application) -> None:
    client: Optional[httpx.AsyncClient] = app.bot_data.get("http")
    if client:
        await client.aclose()
    logger.info("HTTP client cerrado")


async def api_get(context: ContextTypes.DEFAULT_TYPE, params: Dict[str, Any]) -> Dict[str, Any]:
    client: httpx.AsyncClient = context.application.bot_data["http"]
    try:
        r = await client.get(API_URL, params=params)
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, dict) else {"ok": False, "error": "respuesta_invalida"}
    except httpx.ReadTimeout:
        logger.warning("ReadTimeout API params=%s", params)
        return {"ok": False, "error": "timeout"}
    except Exception as e:
        logger.exception("Error API: %s", e)
        return {"ok": False, "error": str(e)}


async def async_noop() -> None:
    return


async def api_with_recovery(
    user_id: str,
    context: ContextTypes.DEFAULT_TYPE,
    payload: Dict[str, Any],
    success_if_retry_error: Optional[str] = None,
) -> Dict[str, Any]:
    resp = await api_get(context, payload)
    logger.info("payload=%s resp=%s", payload.get("accion") or payload.get("api"), resp)

    if resp.get("ok"):
        return resp

    if resp.get("error") != "timeout":
        return resp

    await context.application.create_task(async_noop())
    time.sleep(0.8)

    retry = dict(payload)
    retry["event_id"] = make_event_id(user_id, f'{payload.get("accion", "retry")}-retry')
    resp2 = await api_get(context, retry)
    logger.info("retry payload=%s resp=%s", retry.get("accion") or retry.get("api"), resp2)

    if resp2.get("ok"):
        return resp2

    if success_if_retry_error and resp2.get("error") == success_if_retry_error:
        return {"ok": True, "recovered": True, "warning": success_if_retry_error}

    return resp2


async def consultar_usuario(context: ContextTypes.DEFAULT_TYPE, user_id: str, force: bool = False) -> Dict[str, Any]:
    cached = USER_CACHE.get(user_id)
    if not force and cached and cached.get("expires_at", 0) > time.time():
        return cached["data"]

    data = await api_get(context, {"user": user_id})
    USER_CACHE[user_id] = {"expires_at": time.time() + USER_CACHE_TTL, "data": data}
    return data


async def cargar_materiales(context: ContextTypes.DEFAULT_TYPE, user_id: str, force: bool = False) -> List[Dict[str, Any]]:
    if not force and MATERIAL_CACHE["expires_at"] > time.time():
        return MATERIAL_CACHE["data"]

    data = await api_get(context, {"user": user_id, "api": "materiales"})
    if data.get("ok"):
        materiales = data.get("materiales", [])
        MATERIAL_CACHE["data"] = materiales
        MATERIAL_CACHE["expires_at"] = time.time() + MATERIAL_CACHE_TTL
        return materiales
    return []


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = get_user_id(update)
    user = await consultar_usuario(context, user_id, force=True)

    if not user.get("ok"):
        await update.message.reply_text(f"❌ Usuario no registrado\n🆔 {user_id}")
        return

    es_lider = str(user.get("es_lider", "NO")).upper() == "SI"
    reset_user_state(context, user_id, user.get("nombre"), es_lider)

    await update.message.reply_text(
        f"👋 Hola {user.get('nombre')}",
        reply_markup=menu_principal(es_lider),
    )


async def manejar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return

    texto = update.message.text.strip()
    user_id = get_user_id(update)

    if not context.user_data.get("user_id"):
        user = await consultar_usuario(context, user_id, force=True)
        if not user.get("ok"):
            await update.message.reply_text("❌ Usuario no válido")
            return
        context.user_data["user_id"] = user_id
        context.user_data["nombre"] = user.get("nombre")
        context.user_data["es_lider"] = str(user.get("es_lider", "NO")).upper() == "SI"

    es_lider = bool(context.user_data.get("es_lider", False))
    estado = context.user_data.get("estado", ESTADOS["MENU"])

    if texto == "🟢 Iniciar turno":
        if should_ignore_duplicate(context, "iniciar_turno"):
            return
        context.user_data["inicio"] = now_iso()
        context.user_data["estado"] = ESTADOS["UBICACION"]
        await update.message.reply_text("📍 Selecciona ubicación:", reply_markup=menu_ubicaciones())
        return

    if estado == ESTADOS["UBICACION"]:
        if texto not in UBICACIONES:
            await update.message.reply_text("❌ Selecciona una ubicación válida", reply_markup=menu_ubicaciones())
            return
        context.user_data["ubicacion"] = texto
        context.user_data["estado"] = ESTADOS["BILLING_NUMERO"]
        await update.message.reply_text("🏭 Selecciona Billing:", reply_markup=menu_billing())
        return

    if estado == ESTADOS["BILLING_NUMERO"]:
        if texto not in [f"Billing {i}" for i in range(1, 10)]:
            await update.message.reply_text("❌ Selecciona Billing 1 a Billing 9", reply_markup=menu_billing())
            return

        context.user_data["area"] = texto
        payload = {
            "user": user_id,
            "accion": "inicio_turno",
            "ubicacion": context.user_data.get("ubicacion"),
            "area": context.user_data.get("area"),
            "inicio": context.user_data.get("inicio"),
            "event_id": make_event_id(user_id, "inicio_turno"),
        }

        resp = await api_with_recovery(user_id, context, payload, success_if_retry_error="ya_existe_sesion_activa")
        if not resp.get("ok"):
            context.user_data["estado"] = ESTADOS["MENU"]
            await update.message.reply_text(
                f"❌ No pude iniciar turno\nDetalle: {resp.get('error', 'sin detalle')}",
                reply_markup=menu_principal(es_lider),
            )
            return

        context.user_data["estado"] = ESTADOS["TRABAJANDO"]
        await update.message.reply_text(
            f"⚡ Turno iniciado\n📍 {context.user_data['ubicacion']}\n🏭 {context.user_data['area']}",
            reply_markup=menu_trabajo(es_lider),
        )
        return

    if texto == "🍽 Salida almuerzo":
        if should_ignore_duplicate(context, "salida_almuerzo"):
            return

        payload = {
            "user": user_id,
            "accion": "salida_almuerzo",
            "fin": now_iso(),
            "event_id": make_event_id(user_id, "salida_almuerzo"),
        }
        resp = await api_with_recovery(user_id, context, payload, success_if_retry_error="no_open_session")
        if not resp.get("ok"):
            await update.message.reply_text(
                f"❌ No pude registrar salida almuerzo\nDetalle: {resp.get('error', 'sin detalle')}",
                reply_markup=menu_trabajo(es_lider),
            )
            return

        context.user_data["estado"] = ESTADOS["ALMUERZO"]
        await update.message.reply_text("🍽 Almuerzo iniciado", reply_markup=menu_almuerzo(es_lider))
        return

    if texto == "🔁 Regreso almuerzo":
        if should_ignore_duplicate(context, "regreso_almuerzo"):
            return

        context.user_data["inicio"] = now_iso()
        payload = {
            "user": user_id,
            "accion": "regreso_almuerzo",
            "ubicacion": context.user_data.get("ubicacion"),
            "area": context.user_data.get("area"),
            "inicio": context.user_data.get("inicio"),
            "event_id": make_event_id(user_id, "regreso_almuerzo"),
        }
        resp = await api_with_recovery(user_id, context, payload, success_if_retry_error="ya_existe_sesion_activa")
        if not resp.get("ok"):
            await update.message.reply_text(
                f"❌ No pude registrar regreso almuerzo\nDetalle: {resp.get('error', 'sin detalle')}",
                reply_markup=menu_almuerzo(es_lider),
            )
            return

        context.user_data["estado"] = ESTADOS["TRABAJANDO"]
        await update.message.reply_text("🔁 Regreso de almuerzo registrado", reply_markup=menu_trabajo(es_lider))
        return

    if texto == "📦 Solicitar material":
        if not es_lider:
            await update.message.reply_text("❌ Solo líderes pueden solicitar material")
            return

        materiales = await cargar_materiales(context, user_id, force=True)
        context.user_data["materiales_cache"] = materiales
        context.user_data["estado"] = ESTADOS["MATERIAL_LISTA"]
        await update.message.reply_text("📦 Selecciona material:", reply_markup=menu_materiales(materiales))
        return

    if estado == ESTADOS["MATERIAL_LISTA"]:
        materiales = context.user_data.get("materiales_cache", [])
        codigo = texto.split(" - ")[0].strip()
        material = next((m for m in materiales if str(m.get("codigo")) == codigo), None)
        if not material:
            await update.message.reply_text("❌ Selecciona un material válido", reply_markup=menu_materiales(materiales))
            return

        context.user_data["material_codigo"] = material["codigo"]
        context.user_data["material_nombre"] = material["material"]
        context.user_data["material_stock"] = material.get("stock_actual", 0)
        context.user_data["estado"] = ESTADOS["MATERIAL_CANTIDAD"]

        await update.message.reply_text(
            f'📦 {material["material"]}\nDisponible: {material.get("stock_actual", 0)}\n\nIngresa cantidad a solicitar:'
        )
        return

    if estado == ESTADOS["MATERIAL_CANTIDAD"]:
        if not is_positive_int(texto):
            await update.message.reply_text("❌ Ingresa una cantidad válida en números")
            return

        payload = {
            "user": user_id,
            "accion": "solicitar_material",
            "area": context.user_data.get("area"),
            "codigo": context.user_data.get("material_codigo"),
            "material": context.user_data.get("material_nombre"),
            "cantidad": texto,
            "event_id": make_event_id(user_id, "solicitar_material"),
        }
        resp = await api_with_recovery(user_id, context, payload)
        if not resp.get("ok"):
            await update.message.reply_text(
                f'❌ No pude registrar solicitud\nDetalle: {resp.get("error", "sin detalle")}',
                reply_markup=menu_trabajo(es_lider) if context.user_data.get("estado_prev") != ESTADOS["ALMUERZO"] else menu_almuerzo(es_lider),
            )
            return

        context.user_data["estado"] = ESTADOS["TRABAJANDO"] if context.user_data.get("estado_anterior_material") != ESTADOS["ALMUERZO"] else ESTADOS["ALMUERZO"]
        await update.message.reply_text(
            f'✅ Solicitud registrada\n📦 {context.user_data.get("material_nombre")}\nCantidad: {texto}\nStock restante: {resp.get("stock_despues", "-")}',
            reply_markup=menu_trabajo(es_lider) if context.user_data["estado"] == ESTADOS["TRABAJANDO"] else menu_almuerzo(es_lider),
        )
        return

    if texto == "🔴 Finalizar jornada":
        if should_ignore_duplicate(context, "finalizar_jornada"):
            return

        payload = {
            "user": user_id,
            "accion": "finalizar_jornada",
            "fin": now_iso(),
            "event_id": make_event_id(user_id, "finalizar_jornada"),
        }
        resp = await api_with_recovery(user_id, context, payload, success_if_retry_error="no_open_session")
        if not resp.get("ok"):
            await update.message.reply_text(
                f'❌ No pude finalizar jornada\nDetalle: {resp.get("error", "sin detalle")}',
                reply_markup=menu_trabajo(es_lider),
            )
            return

        resumen = await api_get(context, {"api": "resumen_pago", "user": user_id})
        nombre = context.user_data.get("nombre")
        reset_user_state(context, user_id, nombre, es_lider)

        if resumen.get("ok"):
            msg = (
                "✅ Jornada finalizada\n\n"
                f"🕒 Horas del día: {resumen.get('horas_dia_texto', '0h 00m')}\n"
                f"📅 Acumulado corte actual: {resumen.get('horas_corte_texto', '0h 00m')}\n"
                f"• Ordinarias: {resumen.get('horas_ordinarias_texto', '0h 00m')}\n"
                f"• Extra: {resumen.get('horas_extra_texto', '0h 00m')}\n"
            )
            if resumen.get("pago_disponible"):
                msg += (
                    "\n💰 Pago proyectado:\n"
                    f"• Ordinario: ${resumen.get('pago_ordinario', 0):,.0f}\n"
                    f"• Extra: ${resumen.get('pago_extra', 0):,.0f}\n"
                    f"• Bruto: ${resumen.get('total_bruto', 0):,.0f}\n"
                    f"• Descuento: ${resumen.get('descuento_total', 0):,.0f}\n"
                    f"• Neto: ${resumen.get('total_neto', 0):,.0f}\n"
                )
            else:
                msg += "\n💰 Pago proyectado: pendiente de parametrización por nómina"
        else:
            msg = "✅ Jornada finalizada"

        await update.message.reply_text(msg, reply_markup=menu_principal(es_lider))
        return


def main() -> None:
    app = (
        ApplicationBuilder()
        .token(TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, manejar))

    logger.info("🚀 VOLTAGE BOT ONLINE")
    app.run_polling(drop_pending_updates=True, poll_interval=0.8)


if __name__ == "__main__":
    main()