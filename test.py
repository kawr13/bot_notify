from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.client.default import DefaultBotProperties
from aiogram.enums.parse_mode import ParseMode
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, Message, ContentType
from aiogram.fsm.context import FSMContext
import json
import os
from btsh import urlsender
import asyncio
import quopri
from icecreamconfig import ic, logger
from aiogram import Router

from aiogram.types import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from aiogram.types.web_app_info import WebAppInfo


router = Router()
global_dict = {}

async def get_global():
    return global_dict


async def set_global(dict_):
    global global_dict
    global_dict = dict_


@router.callback_query(F.data.startswith('send_'))
async def editable_relis(call: types.CallbackQuery, state: FSMContext): 
    dict_ = await get_global()
    ic('teeeeest')
    cont_num = call.data.split('_')[1]
    logger.info(cont_num)
    logger.info(dict_)
    for key, value in dict_.items():
        if cont_num in key:
            await urlsender.connect(key.split('_')[0], value)
            result = await asyncio.create_task(urlsender.connect_relise(key.split('_')[0], value))
            ic(result)
            if result:
                try:
                    result = result.replace("<center>", "").replace("</center>", "").replace("<", "")
                    await call.message.answer(result, reply_markup=types.ReplyKeyboardRemove())
                except Exception as e:
                    logger.error(f'Ошибка {e}')
            await call.message.answer('Заявка отправлена', reply_markup=types.ReplyKeyboardRemove())
