
import asyncio
import logging
import math
import os
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import httpx
from telegram import KeyboardButton, ReplyKeyboardMarkup, Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

try:
    from dotenv import load_dotenv  # type: ignore
except ImportError:
    load_dotenv = None

if load_dotenv:
    load_dotenv(override=True)

TOKEN = os.getenv("TOKEN", "").strip()
API_URL = os.getenv("API_URL", "").strip()
HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "12.0"))
HTTP_CONNECT_TIMEOUT = float(os.getenv("HTTP_CONNECT_TIMEOUT", "3.0"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper().strip()

if not TOKEN:
    raise ValueError("Falta la variable de entorno TOKEN")
if not API_URL:
    raise ValueError("Falta la variable de entorno API_URL")

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=getattr(logging, LOG_LEVEL, logging.INFO),
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.INFO)

logger = logging.getLogger("voltage_bot")

PUNTOS_AUTORIZADOS = [
    {"nombre": "Planta principal", "lat": 0.0, "lng": 0.0, "radio_m": 150},
    {"nombre": "Bodega", "lat": 0.0, "lng": 0.0, "radio_m": 150},
    {"nombre": "Obra 1", "lat": 0.0, "lng": 0.0, "radio_m": 150},
    {"nombre": "Obra 2", "lat": 0.0, "lng": 0.0, "radio_m": 150},
    {"nombre": "Obra 3", "lat": 0.0, "lng": 0.0, "radio_m": 150},
]

BILLINGS = [f"Billing {i}" for i in range(1, 10)]

ESTADOS = {
    "MENU": "menu",
    "ESPERANDO_UBICACION": "esperando_ubicacion",
    "BILLING_NUMERO": "billing_numero",
    "TRABAJANDO": "trabajando",
    "ALMUERZO": "almuerzo",
    "MATERIAL_LISTA": "material_lista",
    "MATERIAL_CANTIDAD": "material_cantidad",
    "FINALIZAR_OBSERVACION": "finalizar_observacion",
}

ACCION_VOLVER = "⬅️ Volver"
ACCION_CANCELAR = "❌ Cancelar"
ACCION_COMPARTIR_UBICACION = "📍 Enviar ubicación actual"
ACCION_SIN_MATERIALES = "SIN MATERIALES"

USER_CACHE: Dict[str, Dict[str, Any]] = {}
USER_CACHE_TTL = 30
MATERIAL_CACHE: Dict[str, Any] = {"expires_at": 0.0, "data": []}
MATERIAL_CACHE_TTL = 60


def now_iso() -> str:
    return datetime.now().isoformat()


def get_user_id(update: Update) -> str:
    return str(update.effective_user.id).strip() if update.effective_user else ""


def make_event_id(user_id: str, accion: str) -> str:
    return f"{user_id}-{accion}-{uuid.uuid4().hex[:12]}"


def is_positive_int(texto: str) -> bool:
    return texto.isdigit() and int(texto) > 0


def should_ignore_duplicate(
    context: ContextTypes.DEFAULT_TYPE,
    action_key: str,
    window_seconds: float = 1.3,
) -> bool:
    now_ts = time.time()
    last_key = context.user_data.get("_last_action_key")
    last_ts = context.user_data.get("_last_action_ts", 0.0)
    if last_key == action_key and (now_ts - last_ts) <= window_seconds:
        return True
    context.user_data["_last_action_key"] = action_key
    context.user_data["_last_action_ts"] = now_ts
    return False


def reset_user_state(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: str,
    nombre: Optional[str] = None,
    es_lider: Optional[bool] = None,
) -> None:
    keep = {
        "user_id": user_id,
        "nombre": nombre if nombre is not None else context.user_data.get("nombre"),
        "es_lider": es_lider if es_lider is not None else context.user_data.get("es_lider", False),
    }
    context.user_data.clear()
    context.user_data["estado"] = ESTADOS["MENU"]
    context.user_data["user_id"] = keep["user_id"]
    context.user_data["nombre"] = keep["nombre"]
    context.user_data["es_lider"] = keep["es_lider"]


def menu_principal(es_lider: bool = False) -> ReplyKeyboardMarkup:
    rows = [["🟢 Iniciar turno"]]
    if es_lider:
        rows.append(["📦 Solicitar material"])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def menu_compartir_ubicacion() -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(ACCION_COMPARTIR_UBICACION, request_location=True)],
        [ACCION_CANCELAR],
    ]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, one_time_keyboard=True)


def menu_billing() -> ReplyKeyboardMarkup:
    rows = [
        ["Billing 1", "Billing 2", "Billing 3"],
        ["Billing 4", "Billing 5", "Billing 6"],
        ["Billing 7", "Billing 8", "Billing 9"],
        [ACCION_VOLVER, ACCION_CANCELAR],
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
    rows: List[List[str]] = []
    for m in materiales[:40]:
        rows.append([f'{m["codigo"]} - {m["material"]} ({m["stock_actual"]})'])
    if not rows:
        rows = [[ACCION_SIN_MATERIALES]]
    rows.append([ACCION_VOLVER, ACCION_CANCELAR])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def menu_cantidad_material() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([[ACCION_VOLVER, ACCION_CANCELAR]], resize_keyboard=True)


def menu_finalizar_observacion() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([[ACCION_VOLVER, ACCION_CANCELAR]], resize_keyboard=True)


def get_menu_by_state(es_lider: bool, estado: str) -> ReplyKeyboardMarkup:
    if estado == ESTADOS["ALMUERZO"]:
        return menu_almuerzo(es_lider)
    if estado == ESTADOS["TRABAJANDO"]:
        return menu_trabajo(es_lider)
    return menu_principal(es_lider)


def puntos_autorizados_configurados() -> bool:
    for p in PUNTOS_AUTORIZADOS:
        if abs(float(p.get("lat", 0.0))) > 0.000001 or abs(float(p.get("lng", 0.0))) > 0.000001:
            return True
    return False


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radio = 6371000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    return 2.0 * radio * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))


def resolve_punto_gps(lat: float, lng: float) -> Tuple[bool, str, Optional[Dict[str, Any]], Optional[float]]:
    if not puntos_autorizados_configurados():
        return True, "⚠️ Validación GPS automática desactivada", None, None

    best_punto: Optional[Dict[str, Any]] = None
    best_dist: Optional[float] = None
    for p in PUNTOS_AUTORIZADOS:
        dist = haversine_m(lat, lng, float(p["lat"]), float(p["lng"]))
        if best_dist is None or dist < best_dist:
            best_dist = dist
            best_punto = p

    if best_punto is None or best_dist is None:
        return False, "❌ No pude validar el punto GPS", None, None

    radio = float(best_punto.get("radio_m", 150))
    if best_dist <= radio:
        return True, f"✅ Punto validado: {best_punto['nombre']} ({best_dist:.0f} m)", best_punto, best_dist

    return (
        False,
        f"❌ Fuera del punto autorizado.\n📍 Más cercano: {best_punto['nombre']}\n📏 Distancia: {best_dist:.0f} m\n🎯 Radio permitido: {radio:.0f} m",
        best_punto,
        best_dist,
    )


def build_ubicacion_text(lat: float, lng: float, punto: Optional[Dict[str, Any]]) -> str:
    gps_text = f"GPS {lat:.6f}, {lng:.6f}"
    if punto and punto.get("nombre"):
        return f"{punto['nombre']} | {gps_text}"
    return gps_text


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
        response = await client.get(API_URL, params=params)
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, dict) else {"ok": False, "error": "respuesta_invalida"}
    except httpx.ReadTimeout:
        logger.warning("ReadTimeout API params=%s", params)
        return {"ok": False, "error": "timeout"}
    except Exception as exc:
        logger.exception("Error API: %s", exc)
        return {"ok": False, "error": str(exc)}


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

    await asyncio.sleep(0.8)

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


async def ensure_user_loaded(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Optional[Tuple[str, bool]]:
    user_id = get_user_id(update)
    if context.user_data.get("user_id"):
        return user_id, bool(context.user_data.get("es_lider", False))

    user = await consultar_usuario(context, user_id, force=True)
    if not user.get("ok"):
        if update.effective_message:
            await update.effective_message.reply_text(f"❌ Usuario no registrado\n🆔 {user_id}")
        return None

    es_lider = str(user.get("es_lider", "NO")).upper() == "SI"
    reset_user_state(context, user_id, user.get("nombre"), es_lider)
    return user_id, es_lider


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    loaded = await ensure_user_loaded(update, context)
    if not loaded or not update.message:
        return

    _, es_lider = loaded
    await update.message.reply_text(
        f"👋 Hola {context.user_data.get('nombre')}",
        reply_markup=menu_principal(es_lider),
    )


async def menu_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    loaded = await ensure_user_loaded(update, context)
    if not loaded or not update.message:
        return

    _, es_lider = loaded
    estado = context.user_data.get("estado", ESTADOS["MENU"])
    await update.message.reply_text("📋 Menú actualizado", reply_markup=get_menu_by_state(es_lider, estado))


async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    loaded = await ensure_user_loaded(update, context)
    if not loaded or not update.message:
        return

    user_id, es_lider = loaded
    nombre = context.user_data.get("nombre")
    reset_user_state(context, user_id, nombre, es_lider)
    await update.message.reply_text("❌ Operación cancelada", reply_markup=menu_principal(es_lider))


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    loaded = await ensure_user_loaded(update, context)
    if not loaded or not update.message:
        return

    _, es_lider = loaded
    texto = (
        "Comandos disponibles:\n"
        "/start - iniciar menú\n"
        "/menu - recuperar teclado\n"
        "/cancel - cancelar operación actual\n"
        "/help - ver ayuda\n\n"
        "Flujo:\n"
        "1. Iniciar turno\n"
        "2. Compartir ubicación actual\n"
        "3. Seleccionar Billing\n"
        "4. Salida / regreso almuerzo\n"
        "5. Finalizar jornada con observación\n"
    )
    if es_lider:
        texto += "6. Solicitar material\n"
    await update.message.reply_text(
        texto,
        reply_markup=get_menu_by_state(es_lider, context.user_data.get("estado", ESTADOS["MENU"])),
    )


async def manejar_ubicacion(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.location:
        return

    loaded = await ensure_user_loaded(update, context)
    if not loaded:
        return

    _, es_lider = loaded
    estado = context.user_data.get("estado", ESTADOS["MENU"])

    if estado != ESTADOS["ESPERANDO_UBICACION"]:
        await update.message.reply_text(
            "ℹ️ Ubicación recibida, pero ahora no era necesaria.",
            reply_markup=get_menu_by_state(es_lider, estado),
        )
        return

    lat = float(update.message.location.latitude)
    lng = float(update.message.location.longitude)

    ok, mensaje, punto, _ = resolve_punto_gps(lat, lng)
    if not ok:
        await update.message.reply_text(
            mensaje + "\n\n📍 Vuelve a compartir tu ubicación para iniciar turno.",
            reply_markup=menu_compartir_ubicacion(),
        )
        return

    context.user_data["gps_lat"] = lat
    context.user_data["gps_lng"] = lng
    context.user_data["punto_nombre"] = punto.get("nombre") if punto else None
    context.user_data["ubicacion"] = build_ubicacion_text(lat, lng, punto)
    context.user_data["estado"] = ESTADOS["BILLING_NUMERO"]

    await update.message.reply_text(
        "\n".join(["📍 Ubicación recibida", mensaje, "", "🏭 Selecciona Billing:"]),
        reply_markup=menu_billing(),
    )


async def volver_desde_materiales(update: Update, context: ContextTypes.DEFAULT_TYPE, es_lider: bool) -> None:
    estado_actual = context.user_data.get("estado")
    estado_prev = context.user_data.get("estado_anterior_material", ESTADOS["TRABAJANDO"])

    if estado_actual == ESTADOS["MATERIAL_CANTIDAD"]:
        context.user_data["estado"] = ESTADOS["MATERIAL_LISTA"]
        materiales = context.user_data.get("materiales_cache", [])
        await update.message.reply_text("📦 Selecciona material:", reply_markup=menu_materiales(materiales))
        return

    context.user_data["estado"] = estado_prev
    await update.message.reply_text("↩️ Regresaste al menú anterior", reply_markup=get_menu_by_state(es_lider, estado_prev))


async def cancelar_operacion_actual(update: Update, context: ContextTypes.DEFAULT_TYPE, es_lider: bool) -> None:
    estado = context.user_data.get("estado", ESTADOS["MENU"])

    if estado in (ESTADOS["MATERIAL_LISTA"], ESTADOS["MATERIAL_CANTIDAD"]):
        estado_prev = context.user_data.get("estado_anterior_material", ESTADOS["TRABAJANDO"])
        context.user_data["estado"] = estado_prev
        await update.message.reply_text(
            "❌ Solicitud de material cancelada",
            reply_markup=get_menu_by_state(es_lider, estado_prev),
        )
        return

    if estado == ESTADOS["FINALIZAR_OBSERVACION"]:
        estado_prev = context.user_data.get("estado_pre_finalizar", ESTADOS["TRABAJANDO"])
        context.user_data["estado"] = estado_prev
        await update.message.reply_text("❌ Cierre cancelado", reply_markup=get_menu_by_state(es_lider, estado_prev))
        return

    if estado == ESTADOS["ESPERANDO_UBICACION"]:
        context.user_data["estado"] = ESTADOS["MENU"]
        await update.message.reply_text("❌ Inicio de turno cancelado", reply_markup=menu_principal(es_lider))
        return

    if estado == ESTADOS["BILLING_NUMERO"]:
        context.user_data["estado"] = ESTADOS["ESPERANDO_UBICACION"]
        await update.message.reply_text("❌ Selección de Billing cancelada", reply_markup=menu_compartir_ubicacion())
        return

    await update.message.reply_text("❌ Operación cancelada", reply_markup=get_menu_by_state(es_lider, estado))


async def finalizar_con_observacion(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    es_lider: bool,
    observacion: str,
) -> None:
    if not update.message:
        return

    user_id = get_user_id(update)
    observacion = observacion.strip()

    if observacion in (ACCION_VOLVER, ACCION_CANCELAR):
        if observacion == ACCION_VOLVER:
            estado_prev = context.user_data.get("estado_pre_finalizar", ESTADOS["TRABAJANDO"])
            context.user_data["estado"] = estado_prev
            await update.message.reply_text("↩️ Regresaste al menú anterior", reply_markup=get_menu_by_state(es_lider, estado_prev))
            return

        await cancelar_operacion_actual(update, context, es_lider)
        return

    if len(observacion) < 5:
        await update.message.reply_text(
            "❌ Describe mejor la labor realizada. Mínimo 5 caracteres.",
            reply_markup=menu_finalizar_observacion(),
        )
        return

    payload = {
        "user": user_id,
        "accion": "finalizar_jornada",
        "fin": now_iso(),
        "observacion": observacion,
        "event_id": make_event_id(user_id, "finalizar_jornada"),
    }
    resp = await api_with_recovery(user_id, context, payload, success_if_retry_error="no_open_session")
    if not resp.get("ok"):
        estado_prev = context.user_data.get("estado_pre_finalizar", ESTADOS["TRABAJANDO"])
        context.user_data["estado"] = estado_prev
        await update.message.reply_text(
            f'❌ No pude finalizar jornada\nDetalle: {resp.get("error", "sin detalle")}',
            reply_markup=get_menu_by_state(es_lider, estado_prev),
        )
        return

    resumen = await api_get(context, {"api": "resumen_pago", "user": user_id})
    nombre = context.user_data.get("nombre")
    reset_user_state(context, user_id, nombre, es_lider)

    if resumen.get("ok"):
        msg = (
            "✅ Jornada finalizada\n"
            f"📝 Labor registrada: {observacion}\n\n"
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
        msg = f"✅ Jornada finalizada\n📝 Labor registrada: {observacion}"

    await update.message.reply_text(msg, reply_markup=menu_principal(es_lider))


async def manejar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return

    if update.message.location:
        await manejar_ubicacion(update, context)
        return

    if not update.message.text:
        return

    texto = update.message.text.strip()
    loaded = await ensure_user_loaded(update, context)
    if not loaded:
        return

    user_id, es_lider = loaded
    estado = context.user_data.get("estado", ESTADOS["MENU"])

    if texto == ACCION_CANCELAR:
        await cancelar_operacion_actual(update, context, es_lider)
        return

    if texto == ACCION_VOLVER:
        if estado in (ESTADOS["MATERIAL_LISTA"], ESTADOS["MATERIAL_CANTIDAD"]):
            await volver_desde_materiales(update, context, es_lider)
            return
        if estado == ESTADOS["FINALIZAR_OBSERVACION"]:
            estado_prev = context.user_data.get("estado_pre_finalizar", ESTADOS["TRABAJANDO"])
            context.user_data["estado"] = estado_prev
            await update.message.reply_text("↩️ Regresaste al menú anterior", reply_markup=get_menu_by_state(es_lider, estado_prev))
            return
        if estado == ESTADOS["BILLING_NUMERO"]:
            context.user_data["estado"] = ESTADOS["ESPERANDO_UBICACION"]
            await update.message.reply_text("↩️ Vuelve a compartir tu ubicación.", reply_markup=menu_compartir_ubicacion())
            return
        await update.message.reply_text("ℹ️ No hay una pantalla anterior disponible.", reply_markup=get_menu_by_state(es_lider, estado))
        return

    if texto == "🟢 Iniciar turno":
        if should_ignore_duplicate(context, "iniciar_turno"):
            return
        context.user_data["inicio"] = now_iso()
        context.user_data["estado"] = ESTADOS["ESPERANDO_UBICACION"]
        await update.message.reply_text(
            "📍 Comparte tu ubicación actual para validar el punto de inicio.",
            reply_markup=menu_compartir_ubicacion(),
        )
        return

    if estado == ESTADOS["ESPERANDO_UBICACION"]:
        await update.message.reply_text("📍 Usa el botón de ubicación del teclado para continuar.", reply_markup=menu_compartir_ubicacion())
        return

    if estado == ESTADOS["BILLING_NUMERO"]:
        if texto not in BILLINGS:
            await update.message.reply_text("❌ Selecciona un Billing válido.", reply_markup=menu_billing())
            return

        context.user_data["area"] = texto
        payload = {
            "user": user_id,
            "accion": "inicio_turno",
            "ubicacion": context.user_data.get("ubicacion", ""),
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
        ubicacion = context.user_data.get("ubicacion", "")
        gps_lat = context.user_data.get("gps_lat")
        gps_lng = context.user_data.get("gps_lng")

        partes = ["⚡ Turno iniciado"]
        if ubicacion:
            partes.append(f"📍 Punto: {ubicacion}")
        if gps_lat is not None and gps_lng is not None:
            partes.append(f"🛰 GPS: {gps_lat:.6f},{gps_lng:.6f}")
        partes.append(f"🏭 {context.user_data['area']}")

        await update.message.reply_text("\n".join(partes), reply_markup=menu_trabajo(es_lider))
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
            "ubicacion": context.user_data.get("ubicacion", ""),
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
        context.user_data["estado_anterior_material"] = estado if estado in (ESTADOS["TRABAJANDO"], ESTADOS["ALMUERZO"]) else ESTADOS["TRABAJANDO"]
        context.user_data["estado"] = ESTADOS["MATERIAL_LISTA"]

        if not materiales:
            await update.message.reply_text(
                "📦 No hay materiales activos disponibles en inventario.",
                reply_markup=ReplyKeyboardMarkup([[ACCION_VOLVER, ACCION_CANCELAR]], resize_keyboard=True),
            )
            return

        await update.message.reply_text("📦 Selecciona material:", reply_markup=menu_materiales(materiales))
        return

    if estado == ESTADOS["MATERIAL_LISTA"]:
        materiales = context.user_data.get("materiales_cache", [])

        if texto == ACCION_SIN_MATERIALES or not materiales:
            await update.message.reply_text(
                "📦 No hay materiales para seleccionar.",
                reply_markup=ReplyKeyboardMarkup([[ACCION_VOLVER, ACCION_CANCELAR]], resize_keyboard=True),
            )
            return

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
            f'📦 {material["material"]}\nDisponible: {material.get("stock_actual", 0)}\n\nIngresa cantidad a solicitar:',
            reply_markup=menu_cantidad_material(),
        )
        return

    if estado == ESTADOS["MATERIAL_CANTIDAD"]:
        if not is_positive_int(texto):
            await update.message.reply_text(
                "❌ Ingresa una cantidad válida en números enteros",
                reply_markup=menu_cantidad_material(),
            )
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
            context.user_data["estado"] = ESTADOS["MATERIAL_CANTIDAD"]
            await update.message.reply_text(
                f'❌ No pude registrar solicitud\nDetalle: {resp.get("error", "sin detalle")}',
                reply_markup=menu_cantidad_material(),
            )
            return

        MATERIAL_CACHE["expires_at"] = 0.0
        estado_prev = context.user_data.get("estado_anterior_material", ESTADOS["TRABAJANDO"])
        context.user_data["estado"] = estado_prev

        await update.message.reply_text(
            f'✅ Solicitud registrada\n📦 {context.user_data.get("material_nombre")}\nCantidad: {texto}\nStock restante: {resp.get("stock_despues", "-")}',
            reply_markup=get_menu_by_state(es_lider, estado_prev),
        )
        return

    if texto == "🔴 Finalizar jornada":
        if should_ignore_duplicate(context, "finalizar_jornada"):
            return

        context.user_data["estado_pre_finalizar"] = estado
        context.user_data["estado"] = ESTADOS["FINALIZAR_OBSERVACION"]
        await update.message.reply_text(
            "📝 Describe la labor realizada hoy para cerrar la jornada:",
            reply_markup=menu_finalizar_observacion(),
        )
        return

    if estado == ESTADOS["FINALIZAR_OBSERVACION"]:
        await finalizar_con_observacion(update, context, es_lider, texto)
        return

    await update.message.reply_text(
        "ℹ️ Usa el teclado del bot o /menu para recuperar las opciones.",
        reply_markup=get_menu_by_state(es_lider, estado),
    )


def main() -> None:
    app = (
        ApplicationBuilder()
        .token(TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", menu_cmd))
    app.add_handler(CommandHandler("cancel", cancel_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(MessageHandler(filters.LOCATION, manejar_ubicacion))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, manejar))

    logger.info("🚀 VOLTAGE BOT ONLINE")
    app.run_polling(drop_pending_updates=True, poll_interval=0.8)


if __name__ == "__main__":
    main()
