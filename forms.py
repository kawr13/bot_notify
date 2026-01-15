from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (

    ReplyKeyboardMarkup,

    KeyboardButton,

    InlineKeyboardButton,

    InlineKeyboardMarkup,

)


starting_kb = ReplyKeyboardMarkup(

    keyboard=[

        [

            KeyboardButton(text="/start"),

        ]

    ],

    resize_keyboard=True,

    one_time_keyboard=True,

    input_field_placeholder="Выберите действие",

    selective=True

)


class Auth(StatesGroup):
    start = State()
    login = State()
    password = State()