import random
from rich.text import Text
from textual.widget import Widget

RAIN_CHARS = "ﾊﾐﾋｰｳｼﾅﾓﾆｻﾜﾂｵﾘｱﾎﾃﾏｹﾒｴｶｷﾑﾕﾗｾﾈｽﾀﾇﾍ01234567890"


class MatrixRain(Widget):
    DEFAULT_CSS = """
    MatrixRain {
        layer: rain;
        width: 100%;
        height: 100%;
    }
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._drops: list[dict] = []
        self._w = 0
        self._h = 0

    def on_mount(self):
        self.set_interval(0.08, self.refresh)

    def on_resize(self, event):
        self._w = event.size.width
        self._h = event.size.height
        self._init_drops()

    def _init_drops(self):
        self._drops = []
        for col in range(self._w):
            self._drops.append({
                "col": col,
                "head": random.uniform(-self._h, 0),
                "speed": random.uniform(0.4, 1.2),
                "length": random.randint(4, 18),
                "chars": [random.choice(RAIN_CHARS) for _ in range(self._h + 20)],
            })

    def render(self) -> Text:
        if not self._drops or self._w == 0 or self._h == 0:
            return Text("")

        # Advance drops
        for d in self._drops:
            d["head"] += d["speed"]
            if d["head"] - d["length"] > self._h:
                d["head"] = random.uniform(-d["length"], 0)
                d["speed"] = random.uniform(0.4, 1.2)
                d["length"] = random.randint(4, 18)
                # randomize a few chars as they reset
                for i in random.sample(range(len(d["chars"])), k=min(5, len(d["chars"]))):
                    d["chars"][i] = random.choice(RAIN_CHARS)

        # Build cell grid: (char, style)
        grid: list[list[tuple[str, str]]] = [
            [(" ", "") for _ in range(self._w)] for _ in range(self._h)
        ]

        for d in self._drops:
            col = d["col"]
            if col >= self._w:
                continue
            head = int(d["head"])
            length = d["length"]
            for i in range(length + 1):
                row = head - i
                if not (0 <= row < self._h):
                    continue
                char = d["chars"][row % len(d["chars"])]
                if i == 0:
                    style = "bold bright_white"
                elif i <= 2:
                    style = "#00ff41"
                elif i <= length // 2:
                    style = "#005500"
                else:
                    style = "#003b00"
                grid[row][col] = (char, style)

        text = Text(no_wrap=True, overflow="crop")
        for row in grid:
            for char, style in row:
                if style:
                    text.append(char, style=style)
                else:
                    text.append(char)
            text.append("\n")
        return text
