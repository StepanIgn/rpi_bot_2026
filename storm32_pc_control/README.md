# Storm32 PC Control

Минимальная отдельная программа для управления `Storm32` с ПК.

Что умеет:
- подключение к контроллеру по `COM`/serial
- управление по двум осям `pitch/yaw`
- кнопки `Up/Down/Left/Right`
- управление стрелками с клавиатуры
- `C` для центрирования

## Установка

```bash
cd storm32_pc_control
python -m venv .venv
# Windows:
.venv\Scripts\activate
pip install -r requirements.txt
```

## Запуск

```bash
python app.py
```

## Использование

- выберите `COM`-порт
- проверьте baud rate, обычно `115200`
- нажмите `Connect`
- управляйте кнопками или стрелками клавиатуры
- `C` отправляет центрирование

Программа шлёт в `Storm32` serial-команду `CMD_SETANGLE (#17)`.
