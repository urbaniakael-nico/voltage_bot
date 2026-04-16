from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
import requests
import httpx
import asyncio
import os

# =========================
# 🔑 TOKEN DESDE RAILWAY
# =========================
TOKEN = os.getenv("TOKEN")

if not TOKEN:
    raise ValueError("❌ TOKEN no definido en Railway (Variables)")

API_URL = "https://script.google.com/macros/s/AKfycbwML0xRC_LbRHSTxVuWvJIbRWr1PilhZQgRRNbIo48zoeTBSVguyYtpoErgx0a_Gwe9/exec"

# =========================
# 🚀 CLIENTE GLOBAL
# =========================
client = httpx.AsyncClient(timeout=5)

async def enviar_async(url):
    try:
        await client.get(url)
    except Exception as e:
        print("ERROR ENVIO:", e)

# =========================
# 🔥 MENU
# =========================
def menu(estado):

    if estado == "check-out":
        return ReplyKeyboardMarkup([["🟢 Iniciar turno"]], resize_keyboard=True)

    if estado == "check-in":
        return ReplyKeyboardMarkup([
            ["🍽 Salida almuerzo"],
            ["📦 Solicitar material"],
            ["🔴 Finalizar turno"]
        ], resize_keyboard=True)

    if estado == "salida_almuerzo":
        return ReplyKeyboardMarkup([
            ["🔙 Regreso almuerzo"],
            ["📦 Solicitar material"],
            ["🔴 Finalizar turno"]
        ], resize_keyboard=True)

    return ReplyKeyboardMarkup([
        ["📦 Solicitar material"],
        ["🔴 Finalizar turno"]
    ], resize_keyboard=True)

# =========================
# 🔥 CONSULTAR API
# =========================
def consultar(telefono):
    try:
        r = requests.get(f"{API_URL}?telefono={telefono}", timeout=5)
        data = r.json()

        return {
            "ok": data.get("ok", False),
            "nombre": data.get("nombre", "Usuario"),
            "estado": data.get("estado", "check-out"),
            "rol": data.get("rol", "TECNICO")
        }

    except Exception as e:
        print("ERROR API:", e)
        return {
            "ok": False,
            "estado": "check-out",
            "rol": "TECNICO",
            "nombre": "Usuario"
        }

# =========================
# 🚀 START
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    telefono = str(update.message.chat_id)
    data = consultar(telefono)

    if not data["ok"]:
        await update.message.reply_text(
            f"❌ No estás registrado\n\n🆔 {telefono}"
        )
        return

    await update.message.reply_text(
        f"👋 Hola {data['nombre']}",
        reply_markup=menu(data["estado"])
    )

# =========================
# 🔥 MANEJO CENTRAL
# =========================
async def manejar(update: Update, context: ContextTypes.DEFAULT_TYPE):

    texto = update.message.text
    telefono = str(update.message.chat_id)

    data = consultar(telefono)

    if not data["ok"]:
        await update.message.reply_text("❌ Usuario no válido")
        return

    # 📦 INVENTARIO
    if texto == "📦 Solicitar material":
        await update.message.reply_text(
            f"📦 Inventario:\n{API_URL}?web=material&telefono={telefono}"
        )
        return

    # 🟢 INICIAR TURNO
    if texto == "🟢 Iniciar turno":

        button = KeyboardButton("📍 Enviar ubicación", request_location=True)

        context.user_data["step"] = "ubicacion"

        await update.message.reply_text(
            "📍 Envía tu ubicación",
            reply_markup=ReplyKeyboardMarkup([[button]], resize_keyboard=True)
        )
        return

    # 🔢 AREA
    if context.user_data.get("step") == "area":

        if texto not in [str(i) for i in range(1,10)]:
            await update.message.reply_text("❌ Área inválida (1-9)")
            return

        lat = context.user_data["lat"]
        lng = context.user_data["lng"]

        await update.message.reply_text("⏳ Procesando...")

        await enviar_async(
            f"{API_URL}?telefono={telefono}&accion=check-in&lat={lat}&lng={lng}&area={texto}"
        )

        context.user_data.clear()

        data = consultar(telefono)

        await update.message.reply_text(
            f"✅ Turno iniciado en área {texto}",
            reply_markup=menu(data["estado"])
        )
        return

    # 📝 OBS
    if context.user_data.get("step") == "obs":

        if len(texto) > 500:
            await update.message.reply_text("❌ Máx 500 caracteres")
            return

        await enviar_async(
            f"{API_URL}?telefono={telefono}&accion=obs&texto={texto}"
        )

        context.user_data.clear()

        data = consultar(telefono)

        await update.message.reply_text(
            "✅ Turno finalizado",
            reply_markup=menu(data["estado"])
        )
        return

    # 🍽 ALMUERZO
    if texto == "🍽 Salida almuerzo":

        await update.message.reply_text("⏳ Procesando...")
        await enviar_async(f"{API_URL}?telefono={telefono}&accion=salida_almuerzo")

        data = consultar(telefono)

        await update.message.reply_text(
            "🍽 En almuerzo",
            reply_markup=menu(data["estado"])
        )
        return

    # 🔙 REGRESO
    if texto == "🔙 Regreso almuerzo":

        await update.message.reply_text("⏳ Procesando...")
        await enviar_async(f"{API_URL}?telefono={telefono}&accion=regreso_almuerzo")

        data = consultar(telefono)

        await update.message.reply_text(
            "🔙 Regresaste",
            reply_markup=menu(data["estado"])
        )
        return

    # 🔴 FINALIZAR
    if texto == "🔴 Finalizar turno":

        await update.message.reply_text("⏳ Cerrando turno...")
        await enviar_async(f"{API_URL}?telefono={telefono}&accion=check-out")

        context.user_data["step"] = "obs"

        await update.message.reply_text("📝 Escribe observaciones (máx 500)")
        return

# =========================
# 📍 UBICACION
# =========================
async def ubicacion(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if context.user_data.get("step") != "ubicacion":
        return

    lat = update.message.location.latitude
    lng = update.message.location.longitude

    context.user_data["lat"] = lat
    context.user_data["lng"] = lng
    context.user_data["step"] = "area"

    botones = [
        ["1","2","3"],
        ["4","5","6"],
        ["7","8","9"]
    ]

    await update.message.reply_text(
        "🔢 Selecciona área",
        reply_markup=ReplyKeyboardMarkup(botones, resize_keyboard=True)
    )

# =========================
# 🚀 MAIN CLOUD
# =========================
def main():

    print("🚀 BOT CLOUD VERSION 3 (TOKEN OK)")

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.LOCATION, ubicacion))
    app.add_handler(MessageHandler(filters.TEXT, manejar))

    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()