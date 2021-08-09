import os
import os.path
import json
import telegram as tg
import telegram.ext
import random
import boardgame_api
import bot_utils

if os.path.exists("debug_env.json"):
    import logging

    logging.basicConfig(format="%(asctime)s %(message)s", level=logging.DEBUG)

    with open("debug_env.json") as r:
        os.environ.update(json.load(r))

try:
    os.mkdir(os.path.join("images", "temp"))
except FileExistsError:
    pass

group_thread = tg.ext.DelayQueue()
pm_thread = tg.ext.DelayQueue()


def avoid_spam(f):
    def decorated(update, context):
        context.db.sadd("user-ids", str(update.effective_user.id).encode())
        if update.effective_chat.type == "private":
            pm_thread._queue.put((f, (update, context), {}))
        else:
            group_thread._queue.put((f, (update, context), {}))

    return decorated


def stop_bot(*args):
    group_thread.stop()
    pm_thread.stop()
    exit()


def serialize_bot_data(self):
    self.bot_data["matches"] = {
        k: v.to_dict() for k, v in self.bot_data["matches"].items()
    }


commands = [
    ("/play", "Играть в шахматы"),
    ("/settings", "Настройки бота, такие как анонимный режим и др."),
]
updater = tg.ext.Updater(
    token=os.environ["BOT_TOKEN"],
    use_context=True,
    defaults=tg.ext.Defaults(quote=True),
    arbitrary_callback_data=True,
    persistence=bot_utils.RedisPersistence(
        url=os.environ["REDISCLOUD_URL"],
        store_callback_data=True,
        preprocessor=serialize_bot_data,
    ),
    context_types=tg.ext.ContextTypes(context=bot_utils.RedisContext),
    user_sig_handler=stop_bot,
)
if not updater.dispatcher.bot_data:
    updater.dispatcher.bot_data = {"queue": {"chess": []}, "matches": {}}
else:
    updater.dispatcher.bot_data["matches"] = {
        k: boardgame_api.chess.from_dict(v, k, updater.bot)
        for k, v in updater.dispatcher.bot_data["matches"].items()
    }
boardgame_api.chess.BaseMatch.db = updater.persistence.conn


@avoid_spam
def start(update, context):
    update.effective_chat.send_message(
        text="Привет! Чтобы сыграть, введи команду /play"
    )


@avoid_spam
def unknown(update, context):
    update.effective_message.reply_text("Неизвестная команда")


@avoid_spam
def settings(update, context):
    is_anon = context.db.is_anon(update.effective_user)
    update.effective_message.reply_text(
        """
Опции:
    <i>Анонимный режим</i>: Бот не будет оставлять ваше имя пользователя (начинающееся с @)
        в сообщениях и во вложениях к ним.
    """,
        parse_mode=tg.ParseMode.HTML,
        reply_markup=tg.InlineKeyboardMarkup(
            [
                [
                    tg.InlineKeyboardButton(
                        text=f'Анонимный режим: {"🟢" if is_anon else "🔴"}',
                        callback_data={
                            "target_id": "MAIN",
                            "expected_uid": update.effective_user.id,
                            "command": "ANON_MODE_OFF" if is_anon else "ANON_MODE_ON",
                        },
                    )
                ]
            ]
        ),
    )


@avoid_spam
def boardgame_menu(update, context):
    keyboard = tg.InlineKeyboardMarkup(
        [
            [
                tg.InlineKeyboardButton(
                    text=i["text"],
                    callback_data={
                        "target_id": "MAIN",
                        "command": "NEW",
                        "expected_uid": update.effective_user.id,
                        "game": "chess",
                        "mode": i["code"],
                    },
                )
            ]
            for i in getattr(boardgame_api, "chess").MODES
        ]
    )
    update.effective_message.reply_text("Выберите режим:", reply_markup=keyboard)


@avoid_spam
def button_callback(update, context):
    args = update.callback_query.data

    if type(args) == tg.ext.InvalidCallbackData:
        update.callback_query.answer("Ошибка: информация о сообщении не найдена.")
        return

    if args["expected_uid"] != update.callback_query.from_user.id:
        if args["target_id"] == "MAIN":
            update.callback_query.answer()
        else:
            update.callback_query.answer(
                text=context.bot_data["matches"][args["target_id"]].WRONG_PERSON_MSG,
                show_alert=True,
            )
        return

    if args["target_id"] == "MAIN":
        if args["command"] == "NA":
            update.callback_query.answer(text="Недоступно", show_alert=True)
        elif args["command"] == "ANON_MODE_OFF":
            context.db.anon_mode_off(update.effective_user)
            update.callback_query.answer("Анонимный режим отключен", show_alert=True)
            update.effective_message.edit_reply_markup(
                tg.InlineKeyboardMarkup(
                    [
                        [
                            tg.InlineKeyboardButton(
                                text=f'Анонимный режим: {"🟢" if context.db.is_anon(update.effective_user) else "🔴"}',
                                callback_data={
                                    "target_id": "MAIN",
                                    "expected_uid": update.effective_user.id,
                                    "command": "ANON_MODE_ON",
                                },
                            )
                        ]
                    ]
                )
            )
        elif args["command"] == "ANON_MODE_ON":
            context.db.anon_mode_on(update.effective_user)
            update.callback_query.answer("Анонимный режим включен", show_alert=True)
            update.effective_message.edit_reply_markup(
                tg.InlineKeyboardMarkup(
                    [
                        [
                            tg.InlineKeyboardButton(
                                text=f'Анонимный режим: {"🟢" if context.db.is_anon(update.effective_user) else "🔴"}',
                                callback_data={
                                    "target_id": "MAIN",
                                    "expected_uid": update.effective_user.id,
                                    "command": "ANON_MODE_OFF",
                                },
                            )
                        ]
                    ]
                )
            )
        elif args["command"] == "CHOOSE_MODE":
            keyboard = tg.InlineKeyboardMarkup(
                [
                    [
                        tg.InlineKeyboardButton(
                            text=i["text"],
                            callback_data={
                                "target_id": "MAIN",
                                "command": "NEW",
                                "expected_uid": update.effective_user.id,
                                "game": args["game"],
                                "mode": i["code"],
                            },
                        )
                    ]
                    for i in getattr(boardgame_api, args["game"]).MODES
                ]
            )
            update.effective_message.edit_text(
                text="Выберите режим:", reply_markup=keyboard
            )
        elif args["command"] == "NEW":
            if args["mode"] == "AI":
                update.effective_message.edit_text("Игра найдена")
                new = getattr(boardgame_api, args["game"]).AIMatch(
                    update.effective_user, update.effective_chat.id, bot=context.bot
                )
                context.bot_data["matches"][new.id] = new
                new.init_turn(setup=True)

            elif len(context.bot_data["queue"][args["game"]]) > 0:
                queued_user, queued_chat, queued_msg = context.bot_data["queue"][
                    args["game"]
                ].pop(0)
                if queued_chat == update.effective_chat:
                    new = getattr(boardgame_api, args["game"]).GroupMatch(
                        queued_user,
                        update.effective_user,
                        update.effective_chat.id,
                        bot=context.bot,
                    )
                else:
                    new = getattr(boardgame_api, args["game"]).PMMatch(
                        queued_user,
                        update.effective_user,
                        queued_chat.id,
                        update.effective_chat.id,
                        bot=context.bot,
                    )
                queued_msg.edit_text(text="Игра найдена")
                update.effective_message.edit_text(text="Игра найдена")
                context.bot_data["matches"][new.id] = new
                new.init_turn()
            else:
                keyboard = tg.InlineKeyboardMarkup(
                    [
                        [
                            tg.InlineKeyboardButton(
                                text="Отмена",
                                callback_data={
                                    "target_id": "MAIN",
                                    "command": "CANCEL",
                                    "game": args["game"],
                                    "uid": update.effective_user.id,
                                    "expected_uid": update.effective_user.id,
                                },
                            )
                        ]
                    ]
                )
                update.effective_message.edit_text(
                    text="Ждём игроков...", reply_markup=keyboard
                )
                context.bot_data["queue"][args["game"]].append(
                    (
                        update.effective_user,
                        update.effective_chat,
                        update.effective_message,
                    )
                )
        elif args["command"] == "CANCEL":
            for index, queued in enumerate(context.bot_data["queue"][args["game"]]):
                if queued[0].id == args["uid"]:
                    queued[2].edit_text(text="Поиск игры отменен")
                    del context.bot_data["queue"][args["game"]][index]

    else:
        res = context.bot_data["matches"][args["target_id"]].handle_input(args["args"])
        res = res if res else (None, False)
        if context.bot_data["matches"][args["target_id"]].finished:
            del context.bot_data["matches"][args["target_id"]]
        update.callback_query.answer(text=res[0], show_alert=res[1])


def main():
    updater.dispatcher.add_handler(tg.ext.CallbackQueryHandler(button_callback))
    updater.dispatcher.add_handler(tg.ext.CommandHandler("start", start))
    updater.dispatcher.add_handler(tg.ext.CommandHandler("play", boardgame_menu))
    updater.dispatcher.add_handler(tg.ext.CommandHandler("settings", settings))
    updater.dispatcher.add_handler(
        tg.ext.MessageHandler(tg.ext.filters.Filters.regex("^/"), unknown)
    )
    updater.bot.set_my_commands(commands)

    updater.bot.send_message(chat_id=os.environ["CREATOR_ID"], text="Бот включен")
    updater.start_polling(drop_pending_updates=True)
    updater.idle()


if __name__ == "__main__":
    main()
