SYMBOL = {"BRL": "R$", "USD": "US$", "EUR": "€", "GBP": "£"}
MESES = "jan fev mar abr mai jun jul ago set out nov dez".split()


def num(x, d=0):
    return f"{x:,.{d}f}".replace(",", "§").replace(".", ",").replace("§", ".")


def pct(x, d=0):
    return num(100 * x, d) + "%"


def money(x, cur):
    s = SYMBOL.get(cur, cur)
    if abs(x) >= 1e6:
        return f"{s} {num(x / 1e6, 1)} mi"
    if abs(x) >= 1e4:
        return f"{s} {num(x / 1e3)} mil"
    if abs(x) >= 1e3:
        return f"{s} {num(x / 1e3, 1)} mil"
    return f"{s} {num(x)}"


def month(m):
    y, mm = m.split("-")
    return f"{MESES[int(mm) - 1]}/{y}"
