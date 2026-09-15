"""To Do List's $4 is in hand before any joker's main effect reads the money.

The list pays under `context.before` (card.lua:3491-3499):

    ease_dollars(self.ability.extra.dollars)
    G.GAME.dollar_buffer = (G.GAME.dollar_buffer or 0) + self.ability.extra.dollars

and evaluate_play runs that pass over the whole row before the cards score
(state_events.lua:628-638). Bootstraps (card.lua:4046) and Bull (3936) read
`G.GAME.dollars + G.GAME.dollar_buffer` in the joker_main pass, so both count
the $4 wherever the list sits. The branch has no `not context.blueprint`, so a
Blueprint copying the list pays another $4 in the same pass.

The simulator paid it from the main pass in row order, which left a Bootstraps
to the list's left reading the money from before the hand. Found on the
headless engine: 2MIUP34I, Zodiac Deck, stake 8, row Delayed Gratification /
Bootstraps / Abstract Joker / To Do List / Shoot the Moon, $4 held and a High
Card the list named -- the game scored 46 x 31 = 1426, the shadow 46 x 29 =
1334. The same seed stopped in some runs and not others, because the hand the
list names follows LuaJIT's per-state string hash (see jimbot_sim.hands), and
the stop needs the named hand played with the money one list short of a
multiple of five.
"""

from jimbot_sim.cards import Card, Rank, Suit
from jimbot_sim.game import Action, ActionType, GameState
from jimbot_sim.hands import HandType
from jimbot_sim.jokers import REGISTRY as JOKERS, JokerInstance

ACE = ((Rank.ACE, Suit.SPADES),)          # High Card: 5 + 11 chips, 1 mult


def _game(names, money, named=HandType.HIGH_CARD):
    game = GameState(seed="2MIUP34I", deck="Red Deck")
    for name in names:
        game.gain_joker(JokerInstance(JOKERS[name]))
    game._start_round()
    game.blind.target = 10 ** 12
    game.hands_left = 10
    for joker in game.jokers:
        if joker.name == "To Do List":
            joker.named_hand = named
    game.money = money
    return game


def _play(game, specs=ACE):
    game.hand[:] = [Card(rank, suit) for rank, suit in specs]
    before = game.chips_scored
    game.step(Action(ActionType.PLAY, cards=tuple(range(len(specs)))))
    return game.chips_scored - before


def test_bootstraps_left_of_the_list_counts_its_four_dollars():
    game = _game(["Bootstraps", "To Do List"], money=3)
    assert _play(game) == 16 * (1 + 2)          # $3 + $4 = $7: one lot of +2
    assert game.money == 7


def test_the_order_of_the_row_does_not_matter():
    game = _game(["To Do List", "Bootstraps"], money=3)
    assert _play(game) == 16 * 3


def test_a_blueprint_on_the_list_pays_in_the_same_pass():
    game = _game(["Bootstraps", "Blueprint", "To Do List"], money=2)
    assert _play(game) == 16 * (1 + 4)          # $2 + $4 + $4 = $10
    assert game.money == 10


def test_bull_counts_the_four_dollars_too():
    game = _game(["Bull", "To Do List"], money=1)
    assert _play(game) == (16 + 2 * 5) * 1


def test_a_hand_the_list_does_not_name_pays_nothing():
    game = _game(["Bootstraps", "To Do List"], money=3, named=HandType.PAIR)
    assert _play(game) == 16
    assert game.money == 3
