from aiogram import Bot, Dispatcher, types, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums.parse_mode import ParseMode



bot_token = '6157296966:AAHGpWhVqAi0IyvX4EkitVKEcP-lrebPa5Y'
bot = Bot(token=bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
