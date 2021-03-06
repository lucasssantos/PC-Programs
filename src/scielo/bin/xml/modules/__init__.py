# coding=utf-8
import locale
import os
import gettext

locale_path = os.path.dirname(os.path.realpath(__file__)).replace('\\', '/') + '/../locale'

current_locale, encoding = locale.getdefaultlocale()


if encoding is None:
    encoding = 'UTF-8'
if current_locale is None:
    current_locale = 'en_US'

if not current_locale in os.listdir(locale_path):
    lang, country = current_locale.split('_')
    if lang in os.listdir(locale_path):
        current_locale = lang

if not current_locale in os.listdir(locale_path):
    current_locale = 'en_US'

t = gettext.translation('xpm-xc', locale_path, [current_locale])
_ = t.ugettext
