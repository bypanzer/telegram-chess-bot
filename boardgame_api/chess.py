import PIL.Image
import itertools
import random
import subprocess
import io
import os.path
from telegram import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    InputMediaPhoto,
    InputMediaVideo,
    User,
    Message,
)
import cv2
import numpy

IDSAMPLE = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ-+"
MODES = [{"text": "Против бота", "code": "AI"}, {"text": "Онлайн", "code": "QUICK"}]
FENSYMBOLS = {
    "k": "King",
    "q": "Queen",
    "r": "Rook",
    "b": "Bishop",
    "n": "Knight",
    "p": "Pawn",
}
IMAGES = {}
for name in ["Pawn", "King", "Bishop", "Rook", "Queen", "Knight"]:
    IMAGES[name] = [
        PIL.Image.open(f"images/chess/{color}_{name.lower()}.png")
        for color in ["black", "white"]
    ]
STARTPOS = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


def _group_buttons(obj, n, head_button=False):
    res = []
    for index in range(len(obj)):
        index -= int(head_button)
        if index == -1:
            res.append([obj[0]])
        elif index // n == index / n:
            res.append([obj[index + int(head_button)]])
        else:
            res[-1].append(obj[index + int(head_button)])

    return res


def _short_dict(obj):
    obj = obj.to_dict()
    keys = list(obj.keys())
    for key in keys:
        if not bool(obj[key]) and key not in ["is_bot"]:
            del obj[key]

    return obj


def _fentoimagemap(fen):
    res = {}
    fen = fen[: fen.find(" ")].split("/")
    for line in range(8):
        offset = 0
        for column in range(8):
            if column + offset > 7:
                break
            char = fen[line][column]
            if char.isdigit():
                offset += int(char) - 1
            else:
                res[(column + offset, 7 - line)] = IMAGES[FENSYMBOLS[char.lower()]][
                    char.isupper()
                ]

    return res


def _imgpos(pos):
    return [19 + 60 * pos[0] - pos[0] // 2, 422 - 59 * pos[1]]


def decode_pos(pos):
    return [ord(pos[0]) - 97, int(pos[1]) - 1]


def encode_pos(pos):
    return chr(pos[0] + 97) + str(pos[1] + 1)


def in_bounds(pos):
    return 0 <= pos[0] <= 7 and 0 <= pos[1] <= 7


def from_dict(obj, match_id, bot):
    print(obj)
    player1 = User.de_json(obj["player1"], bot)
    msg = Message.de_json(obj["msg"], bot)
    if "player2" in obj:
        player2 = User.de_json(obj["player2"], bot)
        if not "msg2" in obj:
            res = GroupMatch(
                player1, player2, obj["chat_id"], bot=bot, fen=obj["states"][-1]
            )
            res.msg = msg
        else:
            res = PMMatch(
                player1,
                player2,
                obj["chat_id1"],
                obj["chat_id2"],
                bot=bot,
                fen=obj["states"][-1],
            )
            res.msg1 = msg
            res.msg2 = Message.de_json(obj["msg2"], bot)

    else:
        res = AIMatch(
            player1,
            obj["chat_id1"],
            bot=bot,
            fen=obj["states"][-1] if obj["states"] else STARTPOS,
        )
        res.set_elo(obj["rating"])
        res.msg1 = msg

    res.id = match_id
    res.states = obj["states"]
    if obj.get("rating", "0") is not None:
        res.turn += 1
        res.empty_halfturns += 1
        res.is_white_turn = not res.is_white_turn
    if type(res) == AIMatch:
        res.init_msg_text = res.msg1.caption
    elif type(res) == PMMatch:
        res.init_msg_text = res.msg1.text if res.is_white_turn else res.msg2.text
    elif type(res) == GroupMatch:
        res.init_msg_text = res.msg.text

    return res


class BaseFigure:
    name = "PLACEHOLDER"
    fen_symbol = ["none", "NONE"]

    def __init__(self, pos, match, is_white):
        self.image = IMAGES[type(self).__name__][int(is_white)]
        self.pos = pos
        self.is_white = is_white
        self.match = match
        self.moved = False
        self.fen_symbol = self.fen_symbol[int(is_white)]

    def __str__(self):
        return f"{self.name} на {encode_pos(self.pos)}"

    def get_moves(self):
        return []

    def move(self, pos):
        print(
            self.match.id,
            "::",
            self.match.players[0].name,
            ":",
            encode_pos(self.pos) + encode_pos(pos),
        )
        if pos == self.match.enpassant_pos[1]:
            figure = self.match[self.match.enpassant_pos[0]]
            del self.match[self.match.enpassant_pos[0]]
        else:
            figure = self.match[pos]
            if figure:
                del self.match[pos]

        self.pos = pos
        self.match.enpassant_pos = [None, None]
        self.moved = True
        return figure

    def is_legal(self, move_pos):
        if not in_bounds(move_pos):
            return False

        actual_pos = self.pos
        allied_king = self.match.get_king(self.is_white)
        killed = self.match[move_pos]
        if killed:
            del self.match[move_pos]
        self.pos = move_pos

        res = allied_king.in_check()

        self.pos = actual_pos
        if killed:
            self.match[move_pos] = killed
        return not res


class Pawn(BaseFigure):
    name = "Пешка"
    fen_symbol = ["p", "P"]

    def get_moves(self):
        allies, enemies = (
            (self.match.whites, self.match.blacks)
            if self.is_white
            else (self.match.blacks, self.match.whites)
        )
        positions = []
        direction = 1 if self.is_white else -1
        if [self.pos[0], self.pos[1] + direction] not in [
            i.pos for i in enemies + allies
        ]:
            positions.append([self.pos[0], self.pos[1] + direction])
            if not self.moved and [self.pos[0], self.pos[1] + direction * 2] not in [
                i.pos for i in enemies
            ]:
                positions.append([self.pos[0], self.pos[1] + direction * 2])

        if [self.pos[0] + 1, self.pos[1] + direction] in [i.pos for i in enemies] + [
            self.match.enpassant_pos[1]
        ]:
            positions.append([self.pos[0] + 1, self.pos[1] + direction])

        if [self.pos[0] - 1, self.pos[1] + direction] in [i.pos for i in enemies] + [
            self.match.enpassant_pos[1]
        ]:
            positions.append([self.pos[0] - 1, self.pos[1] + direction])

        moves = []
        for move in positions:
            if in_bounds(move) and move not in [i.pos for i in allies]:
                moves.append(
                    {
                        "pos": move,
                        "killing": move
                        in [i.pos for i in enemies] + [self.match.enpassant_pos[1]],
                    }
                )

        return moves

    def move(self, pos):
        old_pos = self.pos
        killed = super().move(pos)
        if abs(old_pos[1] - pos[1]) == 2:
            self.match.enpassant_pos = [pos, [pos[0], (pos[1] + old_pos[1]) // 2]]

        return killed


class Knight(BaseFigure):
    name = "Конь"
    fen_symbol = ["n", "N"]

    def get_moves(self):
        allies, enemies = (
            (self.match.whites, self.match.blacks)
            if self.is_white
            else (self.match.blacks, self.match.whites)
        )
        moves = []
        for move in [
            [2, -1],
            [2, 1],
            [1, 2],
            [1, -2],
            [-1, 2],
            [-1, -2],
            [-2, 1],
            [-2, -1],
        ]:
            move = [a + b for a, b in zip(move, self.pos)]
            if in_bounds(move) and move not in [i.pos for i in allies]:
                moves.append(
                    {
                        "pos": move,
                        "killing": move
                        in [i.pos for i in enemies] + [self.match.enpassant_pos[1]],
                    }
                )

        return moves


class Rook(BaseFigure):
    name = "Ладья"
    fen_symbol = ["r", "R"]

    def get_moves(self):
        allies, enemies = (
            (self.match.whites, self.match.blacks)
            if self.is_white
            else (self.match.blacks, self.match.whites)
        )
        moves = []
        for move_seq in [
            zip(range(1, 8), [0] * 7),
            zip(range(-1, -8, -1), [0] * 7),
            zip([0] * 7, range(1, 8)),
            zip([0] * 7, range(-1, -8, -1)),
        ]:
            for move in move_seq:
                move = [a + b for a, b in zip(self.pos, move)]
                if move in [i.pos for i in allies] or not in_bounds(move):
                    break
                elif move in [i.pos for i in enemies] + [self.match.enpassant_pos[1]]:
                    moves.append({"pos": move, "killing": True})
                    break
                else:
                    moves.append({"pos": move, "killing": False})

        return moves


class Bishop(BaseFigure):
    name = "Слон"
    fen_symbol = ["b", "B"]

    def get_moves(self):
        allies, enemies = (
            (self.match.whites, self.match.blacks)
            if self.is_white
            else (self.match.blacks, self.match.whites)
        )
        moves = []
        for move_seq in [
            zip(range(1, 8), range(1, 8)),
            zip(range(-1, -8, -1), range(-1, -8, -1)),
            zip(range(-1, -8, -1), range(1, 8)),
            zip(range(1, 8), range(-1, -8, -1)),
        ]:
            for move in move_seq:
                move = [a + b for a, b in zip(self.pos, move)]
                if move in [i.pos for i in allies] or not in_bounds(move):
                    break
                elif move in [i.pos for i in enemies] + [self.match.enpassant_pos[1]]:
                    moves.append({"pos": move, "killing": True})
                    break
                else:
                    moves.append({"pos": move, "killing": False})

        return moves


class Queen(BaseFigure):
    name = "Ферзь"
    fen_symbol = ["q", "Q"]

    def get_moves(self):
        return Bishop.get_moves(self) + Rook.get_moves(self)


class King(BaseFigure):
    name = "Король"
    fen_symbol = ["k", "K"]

    def get_moves(self, for_fen=False):
        allies, enemies = (
            (self.match.whites, self.match.blacks)
            if self.is_white
            else (self.match.blacks, self.match.whites)
        )
        moves = []
        for x in [-1, 0, 1]:
            for y in [-1, 0, 1]:
                move = [self.pos[0] + x, self.pos[1] + y]
                if in_bounds(move) and move not in [i.pos for i in allies]:
                    moves.append(
                        {
                            "pos": move,
                            "killing": move
                            in [i.pos for i in enemies] + [self.match.enpassant_pos[1]],
                        }
                    )
        if not self.moved and not self.in_check():
            Y = 0 if self.is_white else 7
            a_rook = self.match[[0, Y]]
            h_rook = self.match[[7, Y]]
            if (
                all([not self.match[[x, Y]] or for_fen for x in [1, 2, 3]])
                and a_rook
                and not a_rook.moved
            ):
                moves.append({"pos": [2, Y], "killing": False})
            if (
                all([not self.match[[x, Y]] or for_fen for x in [5, 6]])
                and h_rook
                and not h_rook.moved
            ):
                moves.append({"pos": [6, Y], "killing": False})
        return moves

    def in_checkmate(self):
        allies = self.match.whites if self.is_white else self.match.blacks

        checks = []
        for figure in allies:
            actual_pos = figure.pos
            for move in figure.get_moves():
                killed = self.match[move["pos"]]
                if killed:
                    del self.match[move["pos"]]
                figure.pos = move["pos"]

                checks.append(self.in_check())

                figure.pos = actual_pos
                if killed:
                    self.match[move["pos"]] = killed

        return all(checks) if checks else False

    def in_check(self):
        enemies = self.match.blacks if self.is_white else self.match.whites
        enemy_moves = itertools.chain(
            *[i.get_moves() for i in enemies if type(i) != King]
        )

        return self.pos in [i["pos"] for i in enemy_moves]


class BaseMatch:
    BOARD_IMG = PIL.Image.open("images/chess/board.png")
    POINTER_IMG = PIL.Image.open("images/chess/pointer.png")
    WRONG_PERSON_MSG = "Сейчас не ваш ход!"
    db = None

    def __init__(self, bot=None, fen=STARTPOS):
        self.bot = bot
        self.whites = []
        self.blacks = []
        self.states = []
        self.finished = False
        self.id = "".join(random.choices(IDSAMPLE, k=8))
        self.image_filename = f"chess-{self.id}.jpg"
        self.video_filename = f"chess-{self.id}.mp4"
        (
            board,
            self.is_white_turn,
            castlings,
            self.enpassant_pos,
            self.empty_halfturns,
            self.turn,
        ) = [int(i) - 1 if i.isdigit() else i for i in fen.split(" ")]

        self.is_white_turn = self.is_white_turn != "w"

        if self.enpassant_pos == "-":
            self.enpassant_pos = [None, None]
        else:
            self.enpassant_pos = decode_pos(self.enpassant_pos)
            self.enpassant_pos = [
                [self.enpassant_pos[0], int((self.enpassant_pos[1] + 4.5) // 2)],
                self.enpassant_pos,
            ]

        board = board.split("/")
        for line in range(8):
            offset = 0
            for column in range(8):
                if column + offset > 7:
                    break
                char = board[line][column]
                if char.isdigit():
                    offset += int(char) - 1
                else:
                    new = eval(FENSYMBOLS[char.lower()])(
                        [column + offset, 7 - line], self, char.isupper()
                    )
                    K = "K" if new.is_white else "k"
                    Q = "Q" if new.is_white else "q"
                    if type(new) == King and K not in castlings and Q not in castlings:
                        new.moved = True
                    elif (
                        type(new) == Rook
                        and K not in castlings
                        and Q in castlings
                        and new.pos != [0, 0 if new.is_white else 7]
                    ):
                        new.moved = True
                    elif (
                        type(new) == Rook
                        and K in castlings
                        and Q not in castlings
                        and new.pos != [7, 0 if new.is_white else 7]
                    ):
                        new.moved = True
                    getattr(self, "whites" if new.is_white else "blacks").append(new)

    def __getitem__(self, key):
        for figure in self.whites + self.blacks:
            if figure.pos == key:
                return figure

    def __setitem__(self, key, value):
        if isinstance(value, list):
            for figure in self.whites + self.blacks:
                if figure.pos == key:
                    figure.pos = value
        elif isinstance(value, BaseFigure):
            if self[key]:
                del self[key]
            (self.whites if value.is_white else self.blacks).append(value)
        else:
            raise TypeError(
                f"Values can only be subclasses of list or BaseFigure (got {type(value).__name__})"
            )

    def __delitem__(self, key):
        for index, figure in enumerate(
            self.whites if self[key].is_white else self.blacks
        ):
            if figure.pos == key:
                del (self.whites if self[key].is_white else self.blacks)[index]

    def _keyboard(self, data, expected_uid, head_button=False):
        res = []
        for button in data:
            res.append(
                InlineKeyboardButton(
                    text=button["text"],
                    callback_data={
                        "target_id": self.id,
                        "expected_uid": expected_uid,
                        "args": button["data"],
                    },
                )
            )

        if res:
            return InlineKeyboardMarkup(_group_buttons(res, 2, head_button=head_button))
        else:
            return None

    def to_dict(self):
        return {"states": self.states}

    @property
    def figures(self):
        return (
            (self.whites, self.blacks)
            if self.is_white_turn
            else (self.blacks, self.whites)
        )

    def get_king(self, is_white):
        for figure in self.whites if is_white else self.blacks:
            if type(figure) == King:
                return figure

    def init_turn(self, move=[None, None], promotion=""):
        figure = self[move[0]]
        turn_info = {"figure": figure}
        if figure:
            turn_info.update({"from": encode_pos(move[0]), "to": encode_pos(move[1])})
            killed = self[move[0]].move(move[1])
            turn_info.update({"killed": killed})

            y = 0 if figure.is_white else 7
            if type(figure) == King and move[0][0] - move[1][0] == -2:
                self[[7, y]].move([5, y])
                turn_info["castling"] = "kingside"
            elif type(figure) == King and move[0][0] - move[1][0] == 2:
                self[[0, y]].move([3, y])
                turn_info["castling"] = "queenside"
            else:
                turn_info["castling"] = None

            if promotion:
                self[move[1]] = eval(FENSYMBOLS[promotion])(
                    move[1], self, figure.is_white
                )
                self[move[1]].moved = True
                turn_info["promotion"] = promotion
            else:
                turn_info["promotion"] = None

        else:
            turn_info.update({"killed": None, "castling": None, "promotion": None})

        cur_king = self.get_king(not self.is_white_turn)
        if cur_king.in_checkmate():
            turn_info["player_gamestate"] = "checkmate"
            self.finished = True
        elif cur_king.in_check():
            turn_info["player_gamestate"] = "check"
        elif self.empty_halfturns == 50:
            turn_info["player_gamestate"] = "stalemate"
            self.finished = True
        else:
            turn_info["player_gamestate"] = "normal"

        self.is_white_turn = not self.is_white_turn
        if self.is_white_turn:
            self.turn += 1
        if turn_info["killed"] or type(turn_info["figure"]) == Pawn:
            self.empty_halfturns = 0
        else:
            self.empty_halfturns += 1

        self.states.append(self.fen_string())

        return turn_info

    def visualise_board(
        self, selected=[None, None], pointers=[], fen="", special=[], return_bytes=True
    ):
        board = self.BOARD_IMG.copy()
        fen = fen if fen else self.states[-1]
        selected = tuple(selected)

        for pos, image in _fentoimagemap(fen).items():
            board.paste(
                "#00cc36" if pos == selected else image, box=_imgpos(pos), mask=image
            )

        for pointer in pointers:
            board.paste(
                "#cc0000" if pointer["killing"] else "#00cc36",
                box=_imgpos(pointer["pos"]),
                mask=self.POINTER_IMG,
            )

        for pointer in special:
            board.paste(
                "#cc0000" if pointer["killing"] else "#3ba7ff",
                box=_imgpos(pointer["pos"]),
                mask=self.POINTER_IMG,
            )

        if return_bytes:
            board = board.convert(mode="RGB")
            buffer = io.BytesIO()
            board.save(buffer, format="JPEG")
            return buffer.getvalue()
        else:
            return board.convert(mode="RGB")

    def get_video(self):
        path = os.path.join("images", "temp", self.video_filename)
        writer = cv2.VideoWriter(
            path, cv2.VideoWriter_fourcc(*"mp4v"), 15.0, (500, 500)
        )

        for fen in self.states:
            img = self.visualise_board(fen=fen, return_bytes=False)
            img_array = numpy.array(img.getdata(), dtype=numpy.uint8).reshape(
                img.size[1], img.size[0], 3
            )
            temp = (img_array[:, :, 2].copy(), img_array[:, :, 0].copy())
            img_array[:, :, 0], img_array[:, :, 2] = temp
            for i in range(15):
                writer.write(img_array)

        for i in range(15):
            writer.write(img_array)
        img.thumbnail((200, 200))
        thumb_buffer = io.BytesIO()
        img.save(thumb_buffer, format="JPEG")

        writer.release()
        video_data = open(path, "rb").read()
        os.remove(path)
        return video_data, thumb_buffer.getvalue()

    def fen_string(self):
        res = [""] * 8

        for line in range(8):
            for column in range(8):
                figure = self[[column, 7 - line]]
                if figure:
                    res[line] += figure.fen_symbol
                else:
                    if res[line] and res[line][-1].isdigit():
                        res[line] = res[line][:-1] + str(int(res[line][-1]) + 1)
                    else:
                        res[line] += "1"
        res = ["/".join(res)]

        res.append("w" if self.is_white_turn else "b")
        res.append("")
        white_king = self.get_king(True)
        black_king = self.get_king(False)
        if not white_king.moved:
            white_king_moves = [i["pos"] for i in white_king.get_moves(for_fen=True)]
            if [6, 0] in white_king_moves:
                res[-1] += "K"
            if [2, 0] in white_king_moves:
                res[-1] += "Q"
        if not black_king.moved:
            black_king_moves = [i["pos"] for i in black_king.get_moves(for_fen=True)]
            if [6, 7] in black_king_moves:
                res[-1] += "k"
            if [2, 7] in black_king_moves:
                res[-1] += "q"
        if white_king.moved and black_king.moved:
            res[-1] += "-"

        res.append(encode_pos(self.enpassant_pos[1]) if self.enpassant_pos[0] else "-")
        res.append(str(self.empty_halfturns))
        res.append(str(self.turn))
        return " ".join(res)


class GroupMatch(BaseMatch):
    def __init__(self, player1, player2, match_chat, **kwargs):
        self.player1 = player1
        self.player2 = player2
        self.chat_id = match_chat
        self.msg = None
        super().__init__(**kwargs)

    def to_dict(self):
        res = super().to_dict()
        res["player1"] = _short_dict(self.player1)
        res["player2"] = _short_dict(self.player2)
        res["chat_id"] = self.chat_id
        res["msg"] = _short_dict(self.msg)
        return res

    def init_turn(self, move=[None, None], turn_info=None, promotion=""):
        res = (
            turn_info
            if turn_info
            else super().init_turn(move=move, promotion=promotion)
        )
        player, opponent = self.players
        if res["player_gamestate"] == "checkmate":
            msg = f"Игра окончена: шах и мат!\nХодов: {self.turn - 1}.\nПобедитель: {self.db.get_name(opponent)}."
        elif res["player_gamestate"] == "stalemate":
            msg = f"Игра окончена: за последние 50 ходов не было убито ни одной фигуры и не сдвинуто ни одной пешки - ничья!\nХодов: {self.turn - 1}"
        else:
            msg = f"Ход {self.turn}"
            if res["figure"]:
                msg += f"\n{res['figure'].name}{' -> '+eval(FENSYMBOLS[res['promotion']]).name if res['promotion'] else ''}: {res['from']} -> {res['to']}"
                if res["castling"]:
                    msg += f" ({'Короткая' if res['castling'] == 'kingside' else 'Длинная'} рокировка)"
            else:
                msg += "\n"

            if res["killed"]:
                msg += f"\n{res['killed']} убит{'а' if res['killed'].name in ['Пешка', 'Ладья'] else ''}!"
            else:
                msg += "\n"

            if res["player_gamestate"] == "check":
                msg += "\nИгроку поставлен шах!"
            else:
                msg += "\n"

            msg += f"\nХодит { self.db.get_name(player) }; выберите действие:"

        if self.finished:
            video, thumb = self.get_video()
            self.msg.edit_media(
                media=InputMediaVideo(
                    self.get_video(),
                    caption=msg,
                    filename=self.video_filename,
                    thumb=thumb,
                )
            )
        else:
            keyboard = self._keyboard(
                [
                    {"text": "Ходить", "data": ["TURN"]},
                    {"text": "Сдаться", "data": ["SURRENDER"]},
                ],
                player.id,
            )
            self.init_msg_text = msg
            if self.msg:
                self.msg = self.msg.edit_media(
                    media=InputMediaPhoto(
                        self.visualise_board(),
                        caption=msg,
                        filename=self.image_filename,
                    ),
                    reply_markup=keyboard,
                )
            else:
                self.msg = self.bot.send_photo(
                    self.chat_id,
                    self.visualise_board(),
                    caption=msg,
                    filename=self.image_filename,
                    reply_markup=keyboard,
                )

    def handle_input(self, args):
        player, opponent = self.players
        allies, enemies = self.figures
        if args[0] == "INIT_MSG":
            self.msg = self.msg.edit_caption(
                self.init_msg_text,
                reply_markup=self._keyboard(
                    [
                        {"text": "Ходить", "data": ["TURN"]},
                        {"text": "Сдаться", "data": ["SURRENDER"]},
                    ],
                    player.id,
                ),
            )

        if args[0] == "TURN":
            figure_buttons = [{"text": "Назад", "data": ["INIT_MSG"]}]
            for figure in allies:
                if next(
                    filter(figure.is_legal, [i["pos"] for i in figure.get_moves()]),
                    None,
                ):
                    figure_buttons.append(
                        {"text": str(figure), "data": ["CHOOSE_FIGURE", figure.pos]}
                    )

            new_text = self.init_msg_text.split("\n")
            new_text[-1] = f"Ходит {self.db.get_name(player)}; выберите фигуру:"

            self.msg = self.msg.edit_media(
                media=InputMediaPhoto(
                    self.visualise_board(),
                    caption="\n".join(new_text),
                    filename=self.image_filename,
                ),
                reply_markup=self._keyboard(
                    figure_buttons, player.id, head_button=True
                ),
            )

        elif args[0] == "SURRENDER":
            self.finished = True
            video, thumb = self.get_video()
            self.msg = self.msg.edit_media(
                media=InputMediaVideo(
                    video,
                    caption=f"""
Игра окончена: {self.db.get_name(player)} сдался.
Ходов: {self.turn - 1}.
Победитель: {self.db.get_name(opponent)}.""",
                    filename=self.video_filename,
                    thumb=thumb,
                )
            )

        elif args[0] == "CHOOSE_FIGURE":
            dest_buttons = [{"text": "Назад", "data": ["TURN"]}]
            figure = self[args[1]]
            moves = list(
                filter(lambda move: figure.is_legal(move["pos"]), figure.get_moves())
            )
            for move in moves:
                if type(figure) == Pawn and move["pos"][1] == (
                    7 if figure.is_white else 0
                ):
                    dest_buttons.append(
                        {
                            "text": ("❌⏫" if move["killing"] else "⏫")
                            + encode_pos(move["pos"]),
                            "data": ["PROMOTION_MENU", args[1], move["pos"]],
                        }
                    )
                else:
                    dest_buttons.append(
                        {
                            "text": ("❌" if move["killing"] else "")
                            + encode_pos(move["pos"]),
                            "data": ["MOVE", args[1], move["pos"]],
                        }
                    )
            new_text = self.init_msg_text.split("\n")
            new_text[
                -1
            ] = f"Ходит {self.db.get_name(player)}; выберите новое место фигуры:"

            self.msg = self.msg.edit_media(
                media=InputMediaPhoto(
                    self.visualise_board(selected=args[1], pointers=moves),
                    caption="\n".join(new_text),
                    filename=self.image_filename,
                ),
                reply_markup=self._keyboard(dest_buttons, player.id, head_button=True),
            )

        elif args[0] == "PROMOTION_MENU":
            figures = [
                {"text": "Ферзь", "data": ["PROMOTION", args[1], args[2], "q"]},
                {"text": "Конь", "data": ["PROMOTION", args[1], args[2], "n"]},
                {"text": "Слон", "data": ["PROMOTION", args[1], args[2], "b"]},
                {"text": "Ладья", "data": ["PROMOTION", args[1], args[2], "r"]},
            ]
            new_text = self.init_msg_text.split("\n")
            new_text[
                -1
            ] = f"Ходит {self.db.get_name(player)}; выберите фигуру, в которую првератится пешка:"

            self.msg = self.msg.edit_media(
                media=InputMediaPhoto(
                    self.visualise_board(
                        selected=args[1], special=[{"pos": args[2], "killing": False}]
                    ),
                    caption="\n".join(new_text),
                    filename=self.image_filename,
                ),
                reply_markup=self._keyboard(figures, player.id),
            )

        elif args[0] == "PROMOTION":
            self.init_turn(move=args[1:3], promotion=args[3])

        elif args[0] == "MOVE":
            self.init_turn(move=args[1:3])


class PMMatch(BaseMatch):
    def __init__(self, player1, player2, chat1, chat2, **kwargs):
        self.player1 = player1
        self.player2 = player2
        self.chat_id1 = chat1
        self.chat_id2 = chat2
        self.msg1 = None
        self.msg2 = None
        super().__init__(**kwargs)

    @property
    def player_msg(self):
        return self.msg1 if self.is_white_turn else self.msg2

    @player_msg.setter
    def player_msg(self, msg):
        if self.is_white_turn:
            self.msg1 = msg
        else:
            self.msg2 = msg

    @property
    def opponent_msg(self):
        return self.msg2 if self.is_white_turn else self.msg1

    @opponent_msg.setter
    def opponent_msg(self, msg):
        if self.is_white_turn:
            self.msg2 = msg
        else:
            self.msg1 = msg

    @property
    def players(self):
        return (
            (self.player1, self.player2)
            if self.is_white_turn
            else (self.player2, self.player1)
        )

    @property
    def chat_ids(self):
        return (
            (self.chat_id1, self.chat_id2)
            if self.is_white_turn
            else (self.chat_id2, self.chat_id1)
        )

    def to_dict(self):
        res = super().to_dict()
        res["player1"] = _short_dict(self.player1)
        res["player2"] = _short_dict(self.player2)
        res["chat_id1"] = self.chat_id1
        res["chat_id2"] = self.chat_id2
        res["msg"] = _short_dict(self.msg1)
        if self.msg2:
            res["msg2"] = _short_dict(self.msg2)
        return res

    def init_turn(self, move=[None, None], turn_info=None, promotion=""):
        res = (
            turn_info
            if turn_info
            else super().init_turn(move=move, promotion=promotion)
        )
        player, opponent = self.players
        player_chatid, opponent_chatid = self.chat_ids
        if res["player_gamestate"] == "checkmate":
            player_text = (
                opponent_text
            ) = f"Игра окончена: шах и мат!\nХодов: {self.turn - 1}.\nПобедитель: {self.db.get_name(opponent)}."
        elif res["player_gamestate"] == "stalemate":
            player_text = (
                opponent_text
            ) = f"Игра окончена: за последние 50 ходов не было убито ни одной фигуры и не сдвинуто ни одной пешки - ничья!\nХодов: {self.turn - 1}"
        else:
            player_text = f"Ход {self.turn}"
            if res["figure"]:
                player_text += f"\n{res['figure'].name}{' -> '+eval(FENSYMBOLS[res['promotion']]).name if res['promotion'] else ''}: {res['from']} -> {res['to']}"
                if res["castling"]:
                    player_text += f' ({"Короткая" if res["castling"] == "kingside" else "Длинная"} рокировка)'
            else:
                player_text += "\n"

            if res["killed"]:
                player_text += f"\n{res['killed']} игрока {self.db.get_name(player)} убит{'а' if res['killed'].name in ['Пешка', 'Ладья'] else ''}!"
            else:
                player_text += "\n"

            if res["player_gamestate"] == "check":
                player_text += f"\nИгроку {player.name} поставлен шах!"
            else:
                player_text += "\n"

            opponent_text = player_text

            player_text += "\nВыберите действие:"
            opponent_text += f"\nХодит {self.db.get_name(player)}"

        if self.finished:
            video, thumb = self.get_video()
            new_msg = InputMediaVideo(
                video, caption=msg, filename=self.video_filename, thumb=thumb
            )
            self.player_msg = self.player_msg.edit_media(media=new_msg)
            if self.opponent_msg:
                self.opponent_msg = self.opponent_msg.edit_media(media=new_msg)
        else:
            self.init_msg_text = player_text
            keyboard = self._keyboard(
                [
                    {"text": "Ходить", "data": ["TURN"]},
                    {"text": "Сдаться", "data": ["SURRENDER"]},
                ],
                player.id,
            )
            if self.player_msg:
                self.player_msg = self.player_msg.edit_media(
                    media=InputMediaPhoto(
                        self.visualise_board(),
                        caption=player_text,
                        filename=self.image_filename,
                    ),
                    reply_markup=keyboard,
                )
            else:
                self.player_msg = self.bot.send_photo(
                    player_chatid,
                    self.visualise_board(),
                    caption=player_text,
                    filename=self.image_filename,
                    reply_markup=keyboard,
                )

            if opponent_chatid:
                if self.player_msg:
                    self.opponent_msg = self.opponent_msg.edit_media(
                        media=InputMediaPhoto(
                            self.visualise_board(),
                            caption=opponent_text,
                            filename=self.image_filename,
                        )
                    )
                else:
                    self.opponent_msg = self.bot.send_photo(
                        opponent_chatid,
                        self.visualise_board(),
                        caption=opponent_text,
                        filename=self.image_filename,
                    )

    def handle_input(self, args):
        player, opponent = self.players
        allies, enemies = self.figures
        if args[0] == "INIT_MSG":
            self.player_msg = self.player_msg.edit_media(
                media=InputMediaPhoto(
                    self.visualise_board(),
                    caption=self.init_msg_text,
                    filename=self.image_filename,
                ),
                reply_markup=self._keyboard(
                    [
                        {"text": "Ходить", "data": ["TURN"]},
                        {"text": "Сдаться", "data": ["SURRENDER"]},
                    ],
                    player.id,
                ),
            )

        if args[0] == "TURN":
            figure_buttons = [{"text": "Назад", "data": ["INIT_MSG"]}]
            for figure in allies:
                if next(
                    filter(figure.is_legal, [i["pos"] for i in figure.get_moves()]),
                    None,
                ):
                    figure_buttons.append(
                        {"text": str(figure), "data": ["CHOOSE_FIGURE", figure.pos]}
                    )

            new_text = self.init_msg_text.split("\n")
            new_text[-1] = f"Выберите фигуру:"

            self.player_msg = self.player_msg.edit_media(
                media=InputMediaPhoto(
                    self.visualise_board(),
                    caption="\n".join(new_text),
                    filename=self.image_filename,
                ),
                reply_markup=self._keyboard(
                    figure_buttons, player.id, head_button=True
                ),
            )

        elif args[0] == "SURRENDER":
            self.finished = True
            video, thumb = self.get_video()
            for msg in [self.msg1, self.msg2]:
                if msg:
                    msg.edit_media(
                        media=InputMediaVideo(
                            video,
                            caption=f"""
Игра окончена: {self.db.get_name(player)} сдался.
Ходов: {self.turn - 1}.
Победитель: {self.db.get_name(opponent)}.""",
                            filename=self.video_filename,
                            thumb=thumb,
                        )
                    )

        elif args[0] == "CHOOSE_FIGURE":
            dest_buttons = [{"text": "Назад", "data": ["TURN"]}]
            figure = self[args[1]]
            moves = list(
                filter(lambda move: figure.is_legal(move["pos"]), figure.get_moves())
            )
            for move in moves:
                if type(figure) == Pawn and move["pos"][1] == (
                    7 if figure.is_white else 0
                ):
                    dest_buttons.append(
                        {
                            "text": ("❌⏫" if move["killing"] else "⏫")
                            + encode_pos(move["pos"]),
                            "data": ["PROMOTION_MENU", args[1], move["pos"]],
                        }
                    )
                else:
                    dest_buttons.append(
                        {
                            "text": ("❌" if move["killing"] else "")
                            + encode_pos(move["pos"]),
                            "data": ["MOVE", args[1], move["pos"]],
                        }
                    )

            new_text = self.init_msg_text.split("\n")
            new_text[-1] = f"Выберите новое место фигуры:"

            self.player_msg = self.player_msg.edit_media(
                media=InputMediaPhoto(
                    self.visualise_board(selected=args[1], pointers=moves),
                    caption="\n".join(new_text),
                    filename=self.image_filename,
                ),
                reply_markup=self._keyboard(dest_buttons, player.id, head_button=True),
            )

        elif args[0] == "PROMOTION_MENU":
            figures = [
                {"text": "Ферзь", "data": ["PROMOTION", args[1], args[2], "q"]},
                {"text": "Конь", "data": ["PROMOTION", args[1], args[2], "n"]},
                {"text": "Слон", "data": ["PROMOTION", args[1], args[2], "b"]},
                {"text": "Ладья", "data": ["PROMOTION", args[1], args[2], "r"]},
            ]

            new_text = self.init_msg_text.split("\n")
            new_text[-1] = f"Выберите фигуру, в которую првератится пешка:"

            self.player_msg = self.player_msg.edit_media(
                media=InputMediaPhoto(
                    self.visualise_board(
                        selected=args[1], special=[{"pos": args[2], "killing": False}]
                    ),
                    caption="\n".join(new_text),
                    filename=self.image_filename,
                ),
                reply_markup=self._keyboard(figures, player.id),
            )

        elif args[0] == "PROMOTION":
            return self.init_turn(move=args[1:3], promotion=args[3])

        elif args[0] == "MOVE":
            return self.init_turn(move=args[1:3])


class AIMatch(PMMatch):
    def __init__(self, player, chat_id, player2=None, **kwargs):
        ai_player = player2 if player2 else kwargs["bot"].get_me()
        self.ai_rating = None
        super().__init__(player, ai_player, chat_id, 0, **kwargs)
        self.engine_api = subprocess.Popen(
            os.environ["ENGINE_FILENAME"],
            bufsize=1,
            universal_newlines=True,
            shell=True,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
        )

        self.engine_api.stdout.readline()

    def to_dict(self):
        res = super().to_dict()
        del res["player2"]
        del res["chat_id2"]
        res["rating"] = self.ai_rating
        return res

    def set_elo(self, value):
        self.ai_rating = value
        self.engine_api.stdin.write(f"setoption name UCI_Elo value {value}\n")

    def init_turn(self, setup=False, **kwargs):
        if setup:
            self.msg1 = self.bot.send_photo(
                self.chat_id1,
                self.visualise_board(fen=STARTPOS),
                caption="Выберите уровень сложности:",
                filename=self.image_filename,
                reply_markup=self._keyboard(
                    [
                        {"text": "Низкий", "data": ["SKILL_LEVEL", "1350"]},
                        {"text": "Средний", "data": ["SKILL_LEVEL", "1850"]},
                        {"text": "Высокий", "data": ["SKILL_LEVEL", "2350"]},
                        {"text": "Легендарный", "data": ["SKILL_LEVEL", "2850"]},
                    ],
                    self.player1.id,
                ),
            )

        else:
            turn_info = BaseMatch.init_turn(self, **kwargs)
            if self.finished:
                return super().init_turn(turn_info=turn_info)

            self.engine_api.stdin.write(f"position fen {self.states[-1]}\n")
            self.engine_api.stdin.write(f"go depth 2\n")
            for line in self.engine_api.stdout:
                if "bestmove" in line:
                    turn = line.split(" ")[1].strip("\n")
                    break
            return super().init_turn(
                move=[decode_pos(turn[:2]), decode_pos(turn[2:4])],
                promotion=turn[-1] if len(turn) == 5 else "",
            )

    def handle_input(self, args):
        if args[0] == "SKILL_LEVEL":
            self.set_elo(args[1])
            return super().init_turn()
        else:
            return super().handle_input(args)
