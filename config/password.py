# -*- coding: utf-8 -*-
"""ChatGPT 注册后设置密码配置。"""
from config.env_loader import apply_env_overrides

ENABLE_POST_REGISTER_PASSWORD = False
CHATGPT_PASSWORD_MODE = "random"  # random / fixed
CHATGPT_FIXED_PASSWORD = ""
PASSWORD_SETUP_RETRIES = 2

apply_env_overrides(globals(), {
    "ENABLE_POST_REGISTER_PASSWORD": "bool",
    "CHATGPT_PASSWORD_MODE": "str",
    "CHATGPT_FIXED_PASSWORD": "str",
    "PASSWORD_SETUP_RETRIES": "int",
})
