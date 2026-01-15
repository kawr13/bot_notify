import logging
import sys
import os
from datetime import datetime
from logging.handlers import RotatingFileHandler
import traceback
import json
from typing import Any, Optional, Union
import inspect
from pathlib import Path
from dataclasses import is_dataclass, asdict
from colorama import init, Fore, Back, Style
import asyncio
import threading
import contextvars
from icecream import ic
from functools import wraps

init()

context_var = contextvars.ContextVar('logger_context', default={})

class UniversalColorFormatter(logging.Formatter):
    COLORS = {'DEBUG': Fore.CYAN, 'INFO': Fore.GREEN, 'WARNING': Fore.YELLOW, 'ERROR': Fore.RED, 'CRITICAL': Back.RED + Fore.WHITE, 'RESET': Style.RESET_ALL}
    def __init__(self, use_colors=True):
        super().__init__()
        self.use_colors = use_colors
    def format_object(self, obj):
        if isinstance(obj, (dict, list, tuple, set)): return json.dumps(obj, indent=2, ensure_ascii=False)
        elif is_dataclass(obj): return json.dumps(asdict(obj), indent=2, ensure_ascii=False)
        elif isinstance(obj, Exception): return f"{type(obj).__name__}: {str(obj)}\n{''.join(traceback.format_tb(obj.__traceback__))}"
        return str(obj)
    def format(self, record):
        try: asyncio.get_running_loop(); is_async=True; task_name=asyncio.current_task().get_name() if asyncio.current_task() else 'NoTask'
        except RuntimeError: is_async=False; task_name=threading.current_thread().name
        try: context = context_var.get(); [setattr(record, k, v) for k,v in context.items()]
        except: context={}
        frame=inspect.currentframe()
        while frame:
            if frame.f_code.co_filename!=__file__: file_path=Path(frame.f_code.co_filename).name; line_no=frame.f_lineno; func_name=frame.f_code.co_name; break
            frame=frame.f_back
        else: file_path=record.filename; line_no=record.lineno; func_name=record.funcName
        timestamp=datetime.fromtimestamp(record.created).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
        message=self.format_object(record.msg) if isinstance(record.msg,(dict,list,tuple,set,Exception)) or is_dataclass(record.msg) else str(record.msg)
        log_entry=f"{timestamp} | {record.levelname:8} | {'ASYNC' if is_async else 'SYNC'} | {file_path}:{line_no} | {func_name}() | Task: {task_name} | {message}"
        if context: log_entry+=f" | Context: {' | '.join(f'{k}: {v}' for k,v in context.items())}"
        if self.use_colors: log_entry=f"{self.COLORS.get(record.levelname,'')}{log_entry}{self.COLORS['RESET']}"
        if record.exc_info: log_entry+=f"\n{self.COLORS['ERROR']}{''.join(traceback.format_exception(*record.exc_info))}{self.COLORS['RESET']}"
        return log_entry

class UniversalLogger:
    def __init__(self, name="universal_logger", log_file="app.log", max_bytes=10*1024*1024, backup_count=5, level=logging.DEBUG, use_colors=True):
        self.logger=logging.getLogger(name); self.logger.setLevel(level); self.logger.handlers=[]
        console_handler=logging.StreamHandler(sys.stdout); console_handler.setFormatter(UniversalColorFormatter(use_colors)); self.logger.addHandler(console_handler)
        if log_file: file_handler=RotatingFileHandler(log_file,maxBytes=max_bytes,backupCount=backup_count,encoding='utf-8'); file_handler.setFormatter(UniversalColorFormatter(use_colors=False)); self.logger.addHandler(file_handler)
        self.tracked_libraries={}
    def set_context(self, **kwargs): context=context_var.get(); context.update(kwargs); context_var.set(context)
    def clear_context(self): context_var.set({})
    def track_library(self, lib_name: str): self.tracked_libraries[lib_name] = logging.getLogger(lib_name); self.tracked_libraries[lib_name].setLevel(logging.DEBUG); self.tracked_libraries[lib_name].addHandler(self.logger.handlers[0])
    def _log(self, level, msg, *args, **kwargs): getattr(self.logger, level)(msg, *args, **kwargs)
    async def _async_log(self, level, msg, *args, **kwargs): self._log(level, msg, *args, **kwargs)
    def debug(self, msg, *args, **kwargs): self._log('debug', msg, *args, **kwargs)
    def info(self, msg, *args, **kwargs): self._log('info', msg, *args, **kwargs)
    def warning(self, msg, *args, **kwargs): self._log('warning', msg, *args, **kwargs)
    def error(self, msg, *args, **kwargs): self._log('error', msg, *args, **kwargs)
    def critical(self, msg, *args, **kwargs): self._log('critical', msg, *args, **kwargs)
    def exception(self, msg, *args, **kwargs): kwargs['exc_info']=True; self._log('error', msg, *args, **kwargs)
    async def adebug(self, msg, *args, **kwargs): await self._async_log('debug', msg, *args, **kwargs)
    async def ainfo(self, msg, *args, **kwargs): await self._async_log('info', msg, *args, **kwargs)
    async def awarning(self, msg, *args, **kwargs): await self._async_log('warning', msg, *args, **kwargs)
    async def aerror(self, msg, *args, **kwargs): await self._async_log('error', msg, *args, **kwargs)
    async def acritical(self, msg, *args, **kwargs): await self._async_log('critical', msg, *args, **kwargs)
    async def aexception(self, msg, *args, **kwargs): kwargs['exc_info']=True; await self._async_log('error', msg, *args, **kwargs)


logger = UniversalLogger(name='my_aiogram_bot', log_file='bot.log')

