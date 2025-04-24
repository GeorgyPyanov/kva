ADMIN_ID = 1537088229
TOKEN = "7524596937:AAGUiZk6t5MnUdkys1GgLq1hn3FbElsBCK0"

import logging
import secrets
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    PicklePersistence,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)

# ====== Настройки ======
ADMIN_USERNAME = "m0onstoun"
QUEST_DURATION = 90 * 60
PERSISTENCE_FILE = 'bot_data.pkl'

# ====== Логирование ======
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ====== Утилиты ======
def is_admin(user):
    return user.username == ADMIN_USERNAME


def get_state(context: ContextTypes.DEFAULT_TYPE):
    default = {
        "words": {},
        "teams": {},
        "monitors": {},
        "quest_running": False,
        "quest_job": None,  # Не сохраняется, но инициализируется
        "used_words": {},  # Словарь {team: set()} → преобразуем в {team: list()}
        "support_codes": {},
        "supporters": {}
    }
    if "state" not in context.bot_data:
        context.bot_data["state"] = default
    else:
        # Преобразование set → list для used_words (если они есть)
        for team, words in context.bot_data["state"].get("used_words", {}).items():
            if isinstance(words, set):
                context.bot_data["state"]["used_words"][team] = list(words)
        # Преобразование set → dict для members (если они есть)
        for team in context.bot_data["state"].get("teams", {}).values():
            if isinstance(team.get("members"), set):
                team["members"] = {username: None for username in team["members"]}
    return context.bot_data["state"]


async def format_error(msg, usage: str):
    await msg.reply_text(f"Неверный формат.\nИспользование: {usage}")


async def announce(team: str, text: str, application):
    st = application.bot_data["state"]
    for user_id in st["teams"][team]["members"].values():
        if user_id:
            try:
                await application.bot.send_message(chat_id=user_id, text=text)
            except Exception as e:
                logger.error(f"Не удалось отправить сообщение user_id={user_id}: {e}")
    # Оповестить сопровождающего
    for username, t in st["supporters"].items():
        if t == team:
            for team_info in st["teams"].values():
                uid = team_info["members"].get(username)
                if uid:
                    try:
                        await application.bot.send_message(chat_id=uid, text=f"(Сопровождающий) {text}")
                    except:
                        pass


# ====== Команды ======
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    st = get_state(context)
    args = context.args
    user = update.effective_user

    if args:
        code = args[0]
        for team, info in st["teams"].items():
            if info["code"] == code:
                if user.username not in info["members"]:
                    info["members"][user.username] = user.id
                    await update.message.reply_text(f"Вы присоединились к команде «{team}»")
                else:
                    await update.message.reply_text(f"Вы уже в команде «{team}»")
                break
        else:
            team = st["support_codes"].pop(code, None)
            if team:
                st["supporters"][user.username] = team
                await update.message.reply_text(f"Вы назначены сопровождающим команды «{team}»")
            else:
                await update.message.reply_text("Неверный код.")

    await update.message.reply_text("Введите /menu для продолжения.")


async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    st = get_state(context)
    user = update.effective_user
    username = user.username

    role = "admin" if is_admin(user) else "supporter" if username in st["supporters"] else "participant"
    buttons = []

    if role == "admin":
        buttons = [
            [InlineKeyboardButton("Старт квеста", callback_data="start_quest")],
            [InlineKeyboardButton("Стоп квеста", callback_data="end_quest")],
            [InlineKeyboardButton("Управление словами", callback_data="manage_words")],
            [InlineKeyboardButton("Список слов", callback_data="list_words")],
            [InlineKeyboardButton("Управление командами", callback_data="manage_teams")],
            [InlineKeyboardButton("Список команд", callback_data="list_teams")],
            [InlineKeyboardButton("Показать счёт", callback_data="show_score")],
            [InlineKeyboardButton("Сгенерировать код сопровождающего", callback_data="gen_support_code")]
        ]
    elif role == "supporter":
        team = st["supporters"].get(username, "?")
        buttons = [
            [InlineKeyboardButton(f"Моя команда: {team}", callback_data="noop")],
            [InlineKeyboardButton("Выдать бонус", callback_data="give_bonus")],
            [InlineKeyboardButton("Показать счёт", callback_data="show_score")],
        ]
    else:
        in_team = any(username in info["members"] for info in st["teams"].values())
        if not in_team:
            buttons.append([InlineKeyboardButton("Присоединиться к команде", callback_data="join_team")])
        buttons.append([InlineKeyboardButton("Мой счёт", callback_data="show_score")])

    kb = InlineKeyboardMarkup(buttons)
    await update.message.reply_text("Выберите действие:", reply_markup=kb)


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    st = get_state(context)
    query = update.callback_query
    await query.answer()
    data = query.data
    user = query.from_user

    if data == "start_quest" and is_admin(user):
        if st["quest_running"]:
            await query.edit_message_text("Квест уже запущен.")
        else:
            st["quest_running"] = True
            job = context.job_queue.run_once(end_quest, QUEST_DURATION, name="quest_end")
            st["quest_job"] = job
            await query.edit_message_text("Квест запущен. Таймер 1 ч 40 мин.")
            # Уведомление всем участникам
            for team, info in st["teams"].items():
                await announce(team, "Найдите коды и пришлите в бот в формате 6-значного числа. У вас есть ровно 1 "
                                     "час 30 минут. Время пошло.", context.application)
    elif data == "end_quest" and is_admin(user):
        if st["quest_running"]:
            await end_quest(context)
            await query.edit_message_text("Квест завершён.")
        else:
            await query.edit_message_text("Квест не запущен.")
    elif data == "list_words" and is_admin(user):
        text = "Слова и баллы:\n" + "\n".join(f"• {w}: {p} баллов" for w, p in st["words"].items()) or "Список пуст."
        await query.edit_message_text(text)
    elif data == "show_score":
        text = "Счёт:\n" + "\n".join(f"• {t}: {i['score']} баллов" for t, i in st["teams"].items())
        await query.edit_message_text(text)
    elif data == "list_teams" and is_admin(user):
        text = "Команды:\n"
        for t, i in st["teams"].items():
            members = " ".join("@" + u for u in i["members"]) or "—"
            text += f"{t} ({i['score']}): {members}\n"
        await query.edit_message_text(text)
    elif data == "manage_words" and is_admin(user):
        await query.edit_message_text("Добавить слово: /addword <слово> <баллы>\nУдалить слово: /delword <слово>")
    elif data == "manage_teams" and is_admin(user):
        await query.edit_message_text("Добавить команду: /addteam <ИмяКоманды>\nУдалить команду: /delteam <ИмяКоманды>")
    elif data == "join_team":
        await query.edit_message_text("Введите /join <кодКоманды>")
    elif data == "give_bonus":
        await query.edit_message_text("Выдать бонус: /bonus <ИмяКоманды> <баллы>")
    elif data == "gen_support_code" and is_admin(user):
        await query.edit_message_text("Сгенерировать код сопровождающего: /gensupport <ИмяКоманды>")
    elif data == "noop":
        await query.answer()


# Команды управления
async def add_word(update: Update, context: ContextTypes.DEFAULT_TYPE):
    st = get_state(context)
    if not is_admin(update.effective_user): return
    if len(context.args) != 2 or not context.args[1].isdigit():
        return await format_error(update.message, "/addword <слово> <баллы>")
    word, pts = context.args
    st["words"][word] = int(pts)
    await update.message.reply_text(f"Добавлено: {word} = {pts} баллов")


async def del_word(update: Update, context: ContextTypes.DEFAULT_TYPE):
    st = get_state(context)
    if not is_admin(update.effective_user): return
    if len(context.args) != 1:
        return await format_error(update.message, "/delword <слово>")
    word = context.args[0]
    if st["words"].pop(word, None):
        await update.message.reply_text(f"Слово {word} удалено")
    else:
        await update.message.reply_text(f"Слово {word} не найдено")

async def add_team(update: Update, context: ContextTypes.DEFAULT_TYPE):
    st = get_state(context)
    if not is_admin(update.effective_user): return
    if len(context.args) != 1:
        return await format_error(update.message, "/addteam <ИмяКоманды>")
    name = context.args[0]
    if name in st["teams"]:
        return await update.message.reply_text(f"Команда {name} уже есть")
    code = secrets.token_hex(3)
    st["teams"][name] = {"code": code, "members": {}, "score": 0}
    link = f"https://t.me/{context.bot.username}?start={code}"
    kb = [[InlineKeyboardButton("Присоединиться", url=link)]]
    await update.message.reply_text(
        f"Команда {name} создана.\nКод: `{code}`",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode='Markdown'
    )


async def del_team(update: Update, context: ContextTypes.DEFAULT_TYPE):
    st = get_state(context)
    if not is_admin(update.effective_user): return
    if len(context.args) != 1:
        return await format_error(update.message, "/delteam <ИмяКоманды>")
    name = context.args[0]
    if st["teams"].pop(name, None):
        await update.message.reply_text(f"Команда {name} удалена")
    else:
        await update.message.reply_text(f"Команда {name} не найдена")


async def join_team(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)


async def bonus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    st = get_state(context)
    username = update.effective_user.username
    if username not in st["supporters"]:
        return await update.message.reply_text("Вы не сопровождающий.")
    if len(context.args) != 2 or not context.args[1].isdigit():
        return await format_error(update.message, "/bonus <ИмяКоманды> <баллы>")
    team, pts = context.args[0], int(context.args[1])
    if team not in st["teams"]:
        return await update.message.reply_text("Команда не найдена.")
    monitor = st["monitors"].setdefault(username, {})
    given = monitor.get(team, 0)
    if given + pts > 5:
        return await update.message.reply_text("Предел 5 баллов на команду.")
    st["teams"][team]["score"] += pts
    monitor[team] = given + pts
    await update.message.reply_text(f"Бонус +{pts} к «{team}». Всего от вас: {monitor[team]}")
    await announce(team, f"Команда «{team}» получила {pts} бонусных баллов от сопровождающего.", context.application)


async def gensupport(update: Update, context: ContextTypes.DEFAULT_TYPE):
    st = get_state(context)
    if not is_admin(update.effective_user): return
    if len(context.args) != 1:
        return await format_error(update.message, "/gensupport <ИмяКоманды>")
    team = context.args[0]
    if team not in st["teams"]:
        return await update.message.reply_text("Команда не найдена.")
    code = secrets.token_hex(4)
    st["support_codes"][code] = team
    await update.message.reply_text(
        f"Код сопровождающего для {team}: `{code}`",
        parse_mode='Markdown'
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    st = get_state(context)
    if not st["quest_running"]:
        return
    user = update.effective_user.username
    text = update.message.text.strip().split()[0]
    pts = st["words"].get(text)
    if not pts:
        return
    for team, info in st["teams"].items():
        if user in info["members"]:
            used = st["used_words"].setdefault(team, set())
            if text in used:
                await update.message.reply_text("Этот код уже открыт вашей командой.")
                return
            info["score"] += pts
            used.add(text)
            await announce(team, f"Команда «{team}» получила {pts} баллов за «{text}»", context.application)
            break


async def end_quest(context: ContextTypes.DEFAULT_TYPE):
    st = context.bot_data["state"]
    st["quest_running"] = False

    # Удаляем задачу, если она есть
    if "quest_job" in st and st["quest_job"]:
        try:
            st["quest_job"].schedule_removal()
        except Exception as e:
            logger.error(f"Ошибка при удалении задачи: {e}")
        finally:
            st["quest_job"] = None

    # Определяем победителей
    if not st["teams"]:
        msg = "Квест завершен. Нет команд-участников."
    else:
        max_score = max(team["score"] for team in st["teams"].values())
        winners = [name for name, team in st["teams"].items() if team["score"] == max_score]

        if len(winners) == 1:
            msg = f"Победитель - команда «{winners[0]}» с результатом {max_score} баллов."
        else:
            winners_str = ", ".join(f"«{name}»" for name in winners)
            msg = f"Ничья! Команды {winners_str} набрали одинаковое количество баллов: {max_score}."

    # Сбрасываем счёт и использованные слова
    for team_name in st["teams"]:
        st["teams"][team_name]["score"] = 0
        st["used_words"][team_name] = set()

    # Оповещаем всех участников
    for team in st["teams"]:
        await announce(team, msg, context.application)


# ====== Main ======
def main():
    persistence = PicklePersistence(filepath=PERSISTENCE_FILE)
    app = Application.builder().token(TOKEN).persistence(persistence).build()

    # Инициализация данных при первом запуске
    if "state" not in app.bot_data:
        app.bot_data["state"] = {
            "words": {},
            "teams": {},
            "monitors": {},
            "quest_running": False,
            "quest_job": None,
            "used_words": {},
            "support_codes": {},
            "supporters": {}
        }

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", menu))
    app.add_handler(CommandHandler("addword", add_word))
    app.add_handler(CommandHandler("delword", del_word))
    app.add_handler(CommandHandler("addteam", add_team))
    app.add_handler(CommandHandler("delteam", del_team))
    app.add_handler(CommandHandler("join", join_team))
    app.add_handler(CommandHandler("bonus", bonus))
    app.add_handler(CommandHandler("gensupport", gensupport))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling()


if __name__ == "__main__":
    main()