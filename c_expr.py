"""Bounded evaluator for a C-expression subset; never execute generated Python.

This is not a C type checker. Casts, sizeof, width-dependent integer operators
and suffixed integers require manual analysis until a target type model exists.
"""
import math
import re


class Manual(Exception):
    """The expression cannot be evaluated conservatively."""


NUM = r'0[xX][0-9a-fA-F]+[uUlL]*|(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?[uUlLfF]*'
TOKEN = re.compile(rf'{NUM}|[A-Za-z_]\w*(?:\s*(?:\.|->)\s*[A-Za-z_]\w*)*|&&|\|\||==|!=|<=|>=|<<|>>|\S')
PRECEDENCE = {'||': 1, '&&': 2, '|': 3, '^': 4, '&': 5,
              '==': 6, '!=': 6, '<': 7, '>': 7, '<=': 7, '>=': 7,
              '<<': 8, '>>': 8, '+': 9, '-': 9, '*': 10, '/': 10, '%': 10}
MAX_TOKENS = 256
MAX_DEPTH = 64
MAX_BITS = 64


def checked(value):
    if isinstance(value, int):
        if value.bit_length() > MAX_BITS:
            raise Manual('정수 연산 크기 제한')
    elif not isinstance(value, float) or not math.isfinite(value):
        raise Manual('유효하지 않은 숫자')
    return value


def num(text):
    if len(text) > 128:
        raise Manual('숫자 길이 제한')
    if text.lower().startswith('0x'):
        if re.search(r'[uUlL]$', text):
            raise Manual('대상 타입이 필요한 정수 접미사')
        return checked(int(text, 16))
    if re.search(r'[uUlL]$', text):
        raise Manual('대상 타입이 필요한 정수 접미사')
    floating = bool(re.search(r'[.eEfF]', text))
    text = re.sub(r'[fF]$', '', text)
    try:
        value = float(text) if floating else int(text, 8 if len(text) > 1 and text[0] == '0' else 10)
    except ValueError:
        raise Manual('잘못된 숫자') from None
    return checked(value)


class Expression:
    def __init__(self, text, consts, var_of=None):
        if len(text) > 8192:
            raise Manual('식 길이 제한')
        self.tokens = TOKEN.findall(text)
        if len(self.tokens) > MAX_TOKENS:
            raise Manual('식 토큰 수 제한')
        self.i, self.variables, self.numbers = 0, [], []
        self.consts, self.var_of = consts, var_of
        self.tree = self.parse(1, 0)
        if self.i != len(self.tokens):
            raise Manual('못 다루는 식 (형 변환 또는 연산자)')

    def peek(self):
        return self.tokens[self.i] if self.i < len(self.tokens) else ''

    def parse(self, minimum, depth):
        if depth > MAX_DEPTH:
            raise Manual('식 깊이 제한')
        token = self.peek()
        self.i += 1
        if token in ('!', '+', '-'):
            left = ('unary', token, self.parse(11, depth + 1))
        elif token == '(':
            left = self.parse(1, depth + 1)
            if self.peek() != ')':
                raise Manual('괄호 또는 형 변환 식')
            self.i += 1
        elif re.fullmatch(NUM, token):
            value = num(token)
            self.numbers.append(value)
            left = ('number', value)
        elif re.fullmatch(r'[A-Za-z_]\w*(?:\s*(?:\.|->)\s*[A-Za-z_]\w*)*', token):
            name = re.sub(r'\s+', '', token)
            call = self.peek() == '('
            if name in ('sizeof', 'int', 'char', 'short', 'long', 'unsigned', 'signed', 'float', 'double'):
                raise Manual('sizeof 또는 형 변환 식')
            if call:
                level = 0
                while self.i < len(self.tokens):
                    t = self.tokens[self.i]
                    level += (t == '(') - (t == ')')
                    self.i += 1
                    if level == 0:
                        break
                if level:
                    raise Manual('stub 호출 괄호')
            if not call and name in self.consts:
                value = checked(self.consts[name])
                self.numbers.append(value)
                left = ('number', value)
            else:
                if self.var_of is None or (not call and name.isupper()):
                    raise Manual(f'상수 값 모름 ({name})')
                key = self.var_of(name, call)
                if key not in self.variables:
                    self.variables.append(key)
                left = ('variable', key)
        else:
            raise Manual('못 다루는 식')
        while self.peek() in PRECEDENCE and PRECEDENCE[self.peek()] >= minimum:
            op = self.peek()
            self.i += 1
            if op in ('<<', '>>', '&', '|', '^'):
                raise Manual('대상 타입이 필요한 비트 연산')
            right = self.parse(PRECEDENCE[op] + 1, depth + 1)
            left = ('binary', op, left, right)
        return left

    def __call__(self, values):
        return self.evaluate(self.tree, values)

    def unsigned_safe(self, key):
        """Only truth tests and comparisons to nonnegative constants are portable.

        Mixed signed/unsigned expressions need C integer promotions, which this
        evaluator deliberately does not guess.
        """
        def contains(node):
            return ((node[0] == 'variable' and node[1] == key) or
                    (node[0] == 'unary' and contains(node[2])) or
                    (node[0] == 'binary' and (contains(node[2]) or contains(node[3]))))

        def safe(node):
            if not contains(node) or node[0] == 'variable':
                return True
            if node[0] == 'unary':
                return node[1] == '!' and safe(node[2])
            _, op, a, b = node
            if op in ('&&', '||'):
                return safe(a) and safe(b)
            if op in ('==', '!=', '<', '>', '<=', '>='):
                return any(v == ('variable', key) and n[0] == 'number' and n[1] >= 0
                           for v, n in ((a, b), (b, a)))
            return False
        return safe(self.tree)

    def evaluate(self, node, values):
        kind = node[0]
        if kind == 'number':
            return node[1]
        if kind == 'variable':
            return checked(values[node[1]])
        if kind == 'unary':
            value = self.evaluate(node[2], values)
            return checked(int(not value) if node[1] == '!' else value if node[1] == '+' else -value)
        _, op, a, b = node
        left = self.evaluate(a, values)
        if op == '&&' and not left:
            return 0
        if op == '||' and left:
            return 1
        right = self.evaluate(b, values)
        if op in ('&&', '||'):
            return int(bool(right))
        if op in ('==', '!=', '<', '>', '<=', '>='):
            return int({'==': left == right, '!=': left != right, '<': left < right,
                        '>': left > right, '<=': left <= right, '>=': left >= right}[op])
        if op == '+':
            result = left + right
        elif op == '-':
            result = left - right
        elif op == '*':
            result = left * right
        elif op in ('/', '%'):
            if right == 0:
                raise Manual('0으로 나눔')
            if isinstance(left, int) and isinstance(right, int):
                quotient = (abs(left) // abs(right)) * (-1 if (left < 0) != (right < 0) else 1)
                result = quotient if op == '/' else left - quotient * right
            elif op == '/':
                result = left / right
            else:
                raise Manual('정수가 아닌 나머지 연산')
        return checked(result)

