
import logging
import math
import os
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import httpx
from dotenv import load_dotenv
from telegram import (
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

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


def get_menu_by_state(es_lider: bool, estado: str) -> ReplyKeyboardMarkup:
    if estado == ESTADOS["ALMUERZO"]:
        return menu_almuerzo(es_lider)
    if estado == ESTADOS["TRABAJANDO"]:
        return menu_trabajo(es_lider)
    return menu_principal(es_lider)


def reset_user_state(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: str,
    nombre: Optional[str] = None,
    es_lider: Optional[bool] = None,
) -> None:
    context.user_data.clear()
    context.user_data["estado"] = ESTADOS["MENU"]
    context.user_data["user_id"] = user_id
    if nombre is not None:
        context.user_data["nombre"] = nombre
    if es_lider is not None:
        context.user_data["es_lider"] = es_lider


def menu_principal(es_lider: bool = False) -> ReplyKeyboardMarkup:
    rows = [["🟢 Iniciar turno"]]
    if es_lider:
        rows.append(["📦 Solicitar material"])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def menu_ubicacion_actual() -> ReplyKeyboardMarkup:
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
    rows = [[f'{m["codigo"]} - {m["material"]} ({m["stock_actual"]})'] for m in materiales[:40]]
    rows.append([ACCION_VOLVER, ACCION_CANCELAR])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def menu_cantidad_material() -> ReplyKeyboardMarkup:
    rows = [
        ["1", "2", "3"],
        ["5", "10", "20"],
        [ACCION_VOLVER, ACCION_CANCELAR],
    ]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def haversine_distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)

    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * r * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def gps_validation_enabled() -> bool:
    for p in PUNTOS_AUTORIZADOS:
        if abs(float(p["lat"])) > 0.000001 or abs(float(p["lng"])) > 0.000001:
            return True
    return False


def validar_punto_gps(lat: float, lng: float) -> Tuple[bool, str, float]:
    if not gps_validation_enabled():
        return True, f"GPS {lat:.6f}, {lng:.6f}", 0.0

    mejor_punto = None
    mejor_distancia = None

    for p in PUNTOS_AUTORIZADOS:
        dist = haversine_distance_m(lat, lng, float(p["lat"]), float(p["lng"]))
        if mejor_distancia is None or dist < mejor_distancia:
            mejor_distancia = dist
            mejor_punto = p

    if not mejor_punto or mejor_distancia is None:
        return False, "sin_punto", 0.0

    permitido = mejor_distancia <= float(mejor_punto["radio_m"])
    return permitido, str(mejor_punto["nombre"]), round(mejor_distancia, 2)


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
    except Exception as e:
        logger.exception("Error API: %s", e)
        return {"ok": False, "error": str(e)}


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

    await context.application.create_task(asyncio_sleep())
    retry = dict(payload)
    retry["event_id"] = make_event_id(user_id, f'{payload.get("accion", "retry")}-retry')
    resp2 = await api_get(context, retry)
    logger.info("retry payload=%s resp=%s", retry.get("accion") or retry.get("api"), resp2)

    if resp2.get("ok"):
        return resp2

    if success_if_retry_error and resp2.get("error") == success_if_retry_error:
        return {"ok": True, "recovered": True, "warning": success_if_retry_error}

    return resp2


async def asyncio_sleep() -> None:
    import asyncio
    await asyncio.sleep(0.8)


async def consultar_usuario(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: str,
    force: bool = False,
) -> Dict[str, Any]:
    cached = USER_CACHE.get(user_id)
    if not force and cached and cached.get("expires_at", 0) > time.time():
        return cached["data"]

    data = await api_get(context, {"user": user_id})
    USER_CACHE[user_id] = {"expires_at": time.time() + USER_CACHE_TTL, "data": data}
    return data


async def cargar_materiales(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: str,
    force: bool = False,
) -> List[Dict[str, Any]]:
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


async def menu_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = get_user_id(update)
    user = await consultar_usuario(context, user_id, force=False)

    if not user.get("ok"):
        await update.message.reply_text(f"❌ Usuario no registrado\n🆔 {user_id}")
        return

    es_lider = str(user.get("es_lider", "NO")).upper() == "SI"
    nombre = user.get("nombre")
    estado_actual = context.user_data.get("estado", ESTADOS["MENU"]) if context.user_data.get("user_id") == user_id else ESTADOS["MENU"]

    if context.user_data.get("user_id") != user_id:
        reset_user_state(context, user_id, nombre, es_lider)
        estado_actual = ESTADOS["MENU"]
    else:
        context.user_data["nombre"] = nombre
        context.user_data["es_lider"] = es_lider

    await update.message.reply_text(
        f"📋 Menú disponible para {nombre}",
        reply_markup=get_menu_by_state(es_lider, estado_actual),
    )


async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = get_user_id(update)
    user = await consultar_usuario(context, user_id, force=False)

    if not user.get("ok"):
        await update.message.reply_text(f"❌ Usuario no registrado\n🆔 {user_id}")
        return

    es_lider = str(user.get("es_lider", "NO")).upper() == "SI"
    reset_user_state(context, user_id, user.get("nombre"), es_lider)

    await update.message.reply_text(
        "✅ Operación cancelada",
        reply_markup=menu_principal(es_lider),
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Comandos disponibles:\n"
        "/start - Iniciar sesión del bot\n"
        "/menu - Recuperar el teclado actual\n"
        "/cancel - Cancelar el flujo actual y volver al menú"
    )


async def entrar_flujo_materiales(update: Update, context: ContextTypes.DEFAULT_TYPE, es_lider: bool) -> None:
    if not es_lider:
        await update.message.reply_text("❌ Solo líderes pueden solicitar material")
        return

    materiales = await cargar_materiales(context, get_user_id(update), force=True)
    estado_anterior = context.user_data.get("estado", ESTADOS["MENU"])
    context.user_data["estado_anterior_material"] = estado_anterior
    context.user_data["materiales_cache"] = materiales

    if not materiales:
        context.user_data["estado"] = estado_anterior
        await update.message.reply_text(
            "📦 No hay materiales disponibles en inventario.\n\n"
            "Revisa la hoja inventario: stock actual > 0 y activo = SI.",
            reply_markup=get_menu_by_state(es_lider, estado_anterior),
        )
        return

    context.user_data["estado"] = ESTADOS["MATERIAL_LISTA"]
    await update.message.reply_text(
        "📦 Selecciona material:",
        reply_markup=menu_materiales(materiales),
    )


async def manejar_ubicacion(update: Update, context: ContextTypes.DEFAULT_TYPE, es_lider: bool) -> None:
    if not update.message or not update.message.location:
        await update.message.reply_text(
            "📍 Debes usar el botón de ubicación para continuar.",
            reply_markup=menu_ubicacion_actual(),
        )
        return

    lat = update.message.location.latitude
    lng = update.message.location.longitude

    permitido, punto_nombre, distancia = validar_punto_gps(lat, lng)

    context.user_data["ubicacion"] = punto_nombre
    context.user_data["gps_lat"] = lat
    context.user_data["gps_lng"] = lng

    if not permitido:
        await update.message.reply_text(
            "❌ Ubicación fuera del punto autorizado.\n"
            f"📍 Punto más cercano: {punto_nombre}\n"
            f"📏 Distancia: {distancia} m\n\n"
            "No se puede iniciar el turno desde esta ubicación.",
            reply_markup=menu_principal(es_lider),
        )
        context.user_data["estado"] = ESTADOS["MENU"]
        return

    mensaje = "📍 Ubicación recibida\n"
    if gps_validation_enabled():
        mensaje += f"✅ Validado en punto: {punto_nombre}\n📏 Distancia: {distancia} m\n\n"
    else:
        mensaje += "⚠️ Validación GPS automática desactivada\n\n"

    context.user_data["estado"] = ESTADOS["BILLING_NUMERO"]
    await update.message.reply_text(
        mensaje + "🏭 Selecciona Billing:",
        reply_markup=menu_billing(),
    )


async def finalizar_con_observacion(update: Update, context: ContextTypes.DEFAULT_TYPE, es_lider: bool, texto: str) -> None:
    user_id = get_user_id(update)
    observacion = texto.strip()
    if len(observacion) < 5:
        await update.message.reply_text(
            "✍️ Describe mejor la labor realizada. Mínimo 5 caracteres.",
        )
        return

    payload = {
        "user": user_id,
        "accion": "finalizar_jornada",
        "fin": now_iso(),
        "observacion": observacion,
        "event_id": make_event_id(user_id, "finalizar_jornada"),
    }
    resp = await api_with_recovery(
        user_id,
        context,
        payload,
        success_if_retry_error="no_open_session",
    )

    if not resp.get("ok"):
        estado_retorno = context.user_data.get("estado_pre_finalizar", ESTADOS["TRABAJANDO"])
        context.user_data["estado"] = estado_retorno
        await update.message.reply_text(
            f'❌ No pude finalizar jornada\nDetalle: {resp.get("error", "sin detalle")}',
            reply_markup=get_menu_by_state(es_lider, estado_retorno),
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

    user_id = get_user_id(update)

    if not context.user_data.get("user_id"):
        user = await consultar_usuario(context, user_id, force=True)
        if not user.get("ok"):
            if update.message.text:
                await update.message.reply_text("❌ Usuario no válido")
            return
        context.user_data["user_id"] = user_id
        context.user_data["nombre"] = user.get("nombre")
        context.user_data["es_lider"] = str(user.get("es_lider", "NO")).upper() == "SI"
        context.user_data["estado"] = ESTADOS["MENU"]

    es_lider = bool(context.user_data.get("es_lider", False))
    estado = context.user_data.get("estado", ESTADOS["MENU"])
    texto = (update.message.text or "").strip()

    if estado == ESTADOS["ESPERANDO_UBICACION"] and update.message.location:
        await manejar_ubicacion(update, context, es_lider)
        return

    if texto == ACCION_CANCELAR:
        estado_retorno = context.user_data.get("estado_anterior_material", ESTADOS["MENU"])
        if estado == ESTADOS["FINALIZAR_OBSERVACION"]:
            estado_retorno = context.user_data.get("estado_pre_finalizar", ESTADOS["TRABAJANDO"])
        context.user_data["estado"] = estado_retorno if estado in (
            ESTADOS["MATERIAL_LISTA"],
            ESTADOS["MATERIAL_CANTIDAD"],
            ESTADOS["FINALIZAR_OBSERVACION"],
        ) else ESTADOS["MENU"]
        await update.message.reply_text(
            "❌ Operación cancelada",
            reply_markup=get_menu_by_state(es_lider, context.user_data["estado"]),
        )
        return

    if texto == "🟢 Iniciar turno":
        if should_ignore_duplicate(context, "iniciar_turno"):
            return
        context.user_data["inicio"] = now_iso()
        context.user_data["estado"] = ESTADOS["ESPERANDO_UBICACION"]
        await update.message.reply_text(
            "📍 Envía tu ubicación actual para validar el punto de inicio.",
            reply_markup=menu_ubicacion_actual(),
        )
        return

    if estado == ESTADOS["ESPERANDO_UBICACION"]:
        await update.message.reply_text(
            "📍 Usa el botón 'Enviar ubicación actual' para continuar.",
            reply_markup=menu_ubicacion_actual(),
        )
        return

    if texto == ACCION_VOLVER:
        if estado == ESTADOS["MATERIAL_CANTIDAD"]:
            context.user_data["estado"] = ESTADOS["MATERIAL_LISTA"]
            await update.message.reply_text(
                "📦 Selecciona material:",
                reply_markup=menu_materiales(context.user_data.get("materiales_cache", [])),
            )
            return
        if estado == ESTADOS["MATERIAL_LISTA"]:
            estado_prev = context.user_data.get("estado_anterior_material", ESTADOS["MENU"])
            context.user_data["estado"] = estado_prev
            await update.message.reply_text(
                "↩️ Volviste al menú anterior.",
                reply_markup=get_menu_by_state(es_lider, estado_prev),
            )
            return
        if estado == ESTADOS["BILLING_NUMERO"]:
            context.user_data["estado"] = ESTADOS["ESPERANDO_UBICACION"]
            await update.message.reply_text(
                "📍 Envía nuevamente tu ubicación actual.",
                reply_markup=menu_ubicacion_actual(),
            )
            return
        if estado == ESTADOS["FINALIZAR_OBSERVACION"]:
            estado_prev = context.user_data.get("estado_pre_finalizar", ESTADOS["TRABAJANDO"])
            context.user_data["estado"] = estado_prev
            await update.message.reply_text(
                "↩️ Regresaste al menú anterior.",
                reply_markup=get_menu_by_state(es_lider, estado_prev),
            )
            return

    if estado == ESTADOS["BILLING_NUMERO"]:
        if texto not in [f"Billing {i}" for i in range(1, 10)]:
            await update.message.reply_text(
                "❌ Selecciona Billing 1 a Billing 9",
                reply_markup=menu_billing(),
            )
            return

        context.user_data["area"] = texto
        gps_lat = context.user_data.get("gps_lat")
        gps_lng = context.user_data.get("gps_lng")

        payload = {
            "user": user_id,
            "accion": "inicio_turno",
            "ubicacion": context.user_data.get("ubicacion"),
            "area": context.user_data.get("area"),
            "inicio": context.user_data.get("inicio"),
            "gps_lat": gps_lat,
            "gps_lng": gps_lng,
            "event_id": make_event_id(user_id, "inicio_turno"),
        }

        resp = await api_with_recovery(
            user_id,
            context,
            payload,
            success_if_retry_error="ya_existe_sesion_activa",
        )
        if not resp.get("ok"):
            context.user_data["estado"] = ESTADOS["MENU"]
            await update.message.reply_text(
                f"❌ No pude iniciar turno\nDetalle: {resp.get('error', 'sin detalle')}",
                reply_markup=menu_principal(es_lider),
            )
            return

        context.user_data["estado"] = ESTADOS["TRABAJANDO"]
        await update.message.reply_text(
            "⚡ Turno iniciado\n"
            f"📍 Punto: {context.user_data.get('ubicacion', '-')}\n"
            f"🛰️ GPS: {gps_lat:.6f},{gps_lng:.6f}\n"
            f"🏭 {context.user_data['area']}",
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
        resp = await api_with_recovery(
            user_id,
            context,
            payload,
            success_if_retry_error="no_open_session",
        )
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
        resp = await api_with_recovery(
            user_id,
            context,
            payload,
            success_if_retry_error="ya_existe_sesion_activa",
        )
        if not resp.get("ok"):
            await update.message.reply_text(
                f"❌ No pude registrar regreso almuerzo\nDetalle: {resp.get('error', 'sin detalle')}",
                reply_markup=menu_almuerzo(es_lider),
            )
            return

        context.user_data["estado"] = ESTADOS["TRABAJANDO"]
        await update.message.reply_text(
            "🔁 Regreso de almuerzo registrado",
            reply_markup=menu_trabajo(es_lider),
        )
        return

    if texto == "📦 Solicitar material":
        await entrar_flujo_materiales(update, context, es_lider)
        return

    if estado == ESTADOS["MATERIAL_LISTA"]:
        if texto == "SIN MATERIALES":
            await update.message.reply_text(
                "❌ No hay materiales cargados. Usa ⬅️ Volver o ❌ Cancelar.",
                reply_markup=menu_materiales(context.user_data.get("materiales_cache", [])),
            )
            return

        materiales = context.user_data.get("materiales_cache", [])
        codigo = texto.split(" - ")[0].strip()
        material = next((m for m in materiales if str(m.get("codigo")) == codigo), None)
        if not material:
            await update.message.reply_text(
                "❌ Selecciona un material válido",
                reply_markup=menu_materiales(materiales),
            )
            return

        context.user_data["material_codigo"] = material["codigo"]
        context.user_data["material_nombre"] = material["material"]
        context.user_data["material_stock"] = material.get("stock_actual", 0)
        context.user_data["estado"] = ESTADOS["MATERIAL_CANTIDAD"]

        await update.message.reply_text(
            f'📦 {material["material"]}\n'
            f'Disponible: {material.get("stock_actual", 0)}\n\n'
            "Ingresa cantidad a solicitar:",
            reply_markup=menu_cantidad_material(),
        )
        return

    if estado == ESTADOS["MATERIAL_CANTIDAD"]:
        if not is_positive_int(texto):
            await update.message.reply_text(
                "❌ Ingresa una cantidad válida en números",
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
            estado_back = context.user_data.get("estado_anterior_material", ESTADOS["TRABAJANDO"])
            context.user_data["estado"] = ESTADOS["MATERIAL_CANTIDAD"]
            await update.message.reply_text(
                f'❌ No pude registrar solicitud\nDetalle: {resp.get("error", "sin detalle")}',
                reply_markup=menu_cantidad_material(),
            )
            return

        MATERIAL_CACHE["expires_at"] = 0
        estado_prev = context.user_data.get("estado_anterior_material", ESTADOS["TRABAJANDO"])
        context.user_data["estado"] = estado_prev

        await update.message.reply_text(
            f'✅ Solicitud registrada\n'
            f'📦 {context.user_data.get("material_nombre")}\n'
            f'Cantidad: {texto}\n'
            f'Stock restante: {resp.get("stock_despues", "-")}',
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
            reply_markup=ReplyKeyboardMarkup([[ACCION_VOLVER, ACCION_CANCELAR]], resize_keyboard=True),
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
    app.add_handler(MessageHandler(filters.LOCATION, manejar))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, manejar))

    logger.info("🚀 VOLTAGE BOT ONLINE")
    app.run_polling(drop_pending_updates=True, poll_interval=0.8)


if __name__ == "__main__":
    main()
