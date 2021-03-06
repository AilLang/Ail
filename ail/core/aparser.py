import ast as pyast

from os.path import split
from typing import Union

from . import aconfig

from .alex import Token, TokenStream, Lex
from . import asts as ast, test_utils
from .error import error_msg
from .pyast import *
from .tokentype import *

__author__ = 'Jack'

_keywords_uc = (
    'PRINT', 'INPUT',
    'IF', 'THEN', 'BEGIN',
    'END', 'WHILE', 'DO',
    'UNTIL', 'LOOP', 'WEND',
    'FUN', 'IS', 'ELSE', 'ENDIF', 'ELIF', 'LOAD', 'IMPORT',
    'FUNC',
    'STRUCT', 'MOD', 'FOR', 'PROTECTED',
    'ASSERT', 'THROW', 'TRY', 'CATCH', 'FINALLY',
    'XOR', 'MOD',
    'GLOBAL', 'NONLOCAL',
    'EXTENDS', 'AND', 'OR', 'NOT',
    'STATIC', 'PROTECTED', 'PRIVATE', 'IS',
)

_end_signs_uc = ('WEND', 'END', 'ENDIF', 'ELSE', 'ELIF', 'CATCH')

_keywords = tuple([x.lower() for x in _keywords_uc])
_end_signs = tuple([x.lower() for x in _end_signs_uc])

_cmp_op = (
    AIL_EQ, AIL_LARGER, AIL_SMALER,
    AIL_LARGER_EQ, AIL_SMALER_EQ,
    AIL_UEQ, AIL_AUEQ, AIL_AEQ, 
)

_inplace_op_dict = {
    AIL_INP_BIN_AND: ('&', ast.BitOpExprAST, True),
    AIL_INP_BIN_OR: ('|', ast.BitOpExprAST, True),
    AIL_INP_DIV: ('/', ast.MuitDivExprAST, True),
    AIL_INP_LSHIFT: ('<<', ast.BitShiftExprAST, True),
    AIL_INP_MOD: ('mod', ast.ModExprAST, False),
    AIL_INP_MULT: ('*', ast.MuitDivExprAST, True),
    AIL_INP_PLUS: ('+', ast.AddSubExprAST, True),
    AIL_INP_RSHIFT: ('>>', ast.BitShiftExprAST, True),
    AIL_INP_SUB: ('-', ast.AddSubExprAST, True),
    AIL_INP_XOR: ('xor', ast.BinXorExprAST, False),
    AIL_INP_POW: ('**', ast.PowerExprAST, False)
}

_literal_names = ('null', 'true', 'false')

_FROM_MAIN = 0
_FROM_FUNC = 1

_class_name_stack = list()

_special_method_map = {
    'new': '__new__',
    'init': '__init__',
    'del': '__del__',
    'getattr': '__getattr__',
    'setattr': '__setattr__',
    'delattr': '__delattr__',
    'getitem': '__getitem__',
    'setitem': '__setitem__',
    'delitem': '__delitem__',
    '__str__': '__str__',
    '__repr__': '__repr__',
}


def _make_private_name(name):
    return '$'.join(_class_name_stack)


class ParserState:
    def __init__(self, cursor: int, level: int, parent_level: int, parser: 'Parser'):
        self.cursor = cursor
        self.level = level
        self.parent_level = parent_level
        self.parser = parser


class Parser:
    def __init__(self):
        self.__filename = '<NO FILE>'
        self.__source = '\n'
        self.__tok_stream = None
        self.__tok_list = None

        self.__tc = 0

        self.__level = 0  # level 0
        self.__parenthesis_level = 0

        self.__pyc_mode = False

    def get_state(self) -> ParserState:
        return ParserState(self.__tc, self.__level, self.__parenthesis_level, self)

    def set_state(self, state: ParserState):
        self.__tc, self.__level, self.__parenthesis_level = \
            state.cursor, state.level, state.parent_level

    def __mov_tp(self, step=1):
        self.__tc += step

    def __next_tok(self,
                   ignore_newline: bool = False,
                   convert_semi: bool = True, just_next: bool = False) -> Token:
        if just_next:
            self.__tc += 1
            if convert_semi and self.__now_tok.ttype == AIL_SEMI:
                self.__now_tok.ttype = AIL_ENTER
            return

        if self.__parenthesis_level > 0:
            self.__skip_newlines()

        if self.__now_tok.ttype in (AIL_SLBASKET, AIL_MLBASKET):
            self.__parenthesis_level += 1
        elif self.__now_tok.ttype in (AIL_SRBASKET, AIL_MRBASKET) and \
                self.__parenthesis_level > 0:
            self.__parenthesis_level -= 1

        self.__tc += 1

        if convert_semi and self.__now_tok.ttype == AIL_SEMI:
            self.__now_tok.ttype = AIL_ENTER

        if self.__now_tok.ttype == AIL_ENTER and ignore_newline:
            self.__next_tok(ignore_newline, convert_semi)

        if self.__parenthesis_level > 0:
            self.__skip_newlines()

        return self.__tok_stream[self.__tc]

    def __skip_newlines(self):
        while self.__now_tok == '\n':
            self.__next_tok(just_next=True)

    def __peek(self, step=1) -> Token:
        return self.__tok_stream[self.__tc + step]

    def __parse_bin_expr(self) -> ast.AddSubExprAST:
        pass

    @property
    def __now_tok(self) -> Token:
        tok = self.__tok_stream[self.__tc]

        if len(_class_name_stack) > 0 and tok.ttype == AIL_IDENTIFIER \
                and tok.value not in _keywords:
            val = tok.value
            if not self.__pyc_mode and \
                    val[:2] == '__' and val[-2:] != '__' and len(_class_name_stack) > 0:
                tok.value = '%s$%s' % (_make_private_name(val), val)
        return tok

    @property
    def __now_ln(self) -> int:
        try:
            return self.__now_tok.ln
        except AttributeError:
            return -1

    def __tok_is(self, tok: Token, value: str) -> bool:
        return tok.ttype != AIL_STRING and tok.value == value

    def __syntax_error(self, msg=None, ln: int = 0):
        error_msg(
            self.__now_ln if ln <= 0 else ln,
            'SyntaxError%s' % ((': ' if msg else '') + (msg if msg else '')),
            self.__filename, source=self.__source)

    def __expect_newline(self):
        if self.__now_tok.ttype != AIL_ENTER:
            self.__syntax_error('except NEWLINE')

    def __parse_arg_item(
            self, type_comment: bool = False, try_tuple: bool = False) -> ast.ArgItemAST:
        star = False
        kw_star = False

        if self.__now_tok.ttype == AIL_MULT:
            self.__next_tok()  # eat '*'
            star = True
        elif self.__now_tok.ttype == AIL_POW:
            self.__next_tok()  # eat '**'
            kw_star = True

        self.__skip_newlines()
        expr = self.__parse_binary_expr(do_tuple=try_tuple, no_assign=True)

        default = None

        if type_comment:
            self.__parse_type_comment()

        if self.__now_tok.ttype == AIL_ASSI:
            self.__next_tok()  # eat '='
            default = self.__parse_binary_expr(do_tuple=False, no_assign=True)

        arg = ast.ArgItemAST(expr, star, self.__now_ln)
        arg.kw_star = kw_star
        arg.default = default

        return arg

    def __check_as_arg_list(self, args: ast.ArgListAST):
        args = args.arg_list

        for arg in args:
            if arg.default and \
                    not (isinstance(arg.expr, ast.CellAST) and arg.expr.type == AIL_IDENTIFIER):
                self.__syntax_error(ln=arg.ln)

    def __parse_param_list(self) -> ast.ArgListAST:
        ln = self.__now_ln

        alist = []

        while self.__now_tok.ttype != AIL_SRBASKET:
            self.__skip_newlines()

            a = self.__parse_arg_item(True)
            alist.append(a)

            self.__skip_newlines()

            if self.__now_tok.ttype == AIL_SRBASKET:
                break

            if self.__now_tok.ttype != AIL_COMMA:
                self.__syntax_error()

            self.__next_tok()  # eat ','

            self.__skip_newlines()

        self.__next_tok()  # eat ')'

        self.__check_as_param_list(alist)

        return ast.ArgListAST(alist, ln)

    def __check_as_param_list(self, param_list: List[ast.ArgItemAST]):
        can_keyword = True
        can_var = True
        can_single = True
        can_default = True

        for param in param_list:
            if not isinstance(param.expr, ast.CellAST) or \
                    param.expr.type != AIL_IDENTIFIER:
                self.__syntax_error(ln=param.ln)

            if not can_var and param.star:
                self.__syntax_error(ln=param.ln)
            elif not can_single and not (param.star or param.kw_star):
                self.__syntax_error(ln=param.ln)
            elif not can_keyword and param.kw_star:
                self.__syntax_error(ln=param.ln)
            elif not can_default and param.default is not None:
                self.__syntax_error(ln=param.ln)

            if param.kw_star and param.star:
                self.__syntax_error(ln=param.ln)
            elif (param.star or param.kw_star) and param.default is not None:
                self.__syntax_error(ln=param.ln)

            if param.default is not None:
                can_single = False

            if param.kw_star:
                can_keyword = False
                can_var = False
                can_single = False
                can_default = False

            if param.star:
                can_var = False

    def __parse_arg_list(self, try_tuple: bool = False) -> ast.ArgListAST:
        alist = []

        if self.__now_tok.ttype != AIL_SRBASKET:
            a = self.__parse_arg_item(True, try_tuple)
            alist.append(a)
        else:
            return ast.ArgListAST(alist, self.__now_ln)

        while self.__now_tok.ttype != AIL_SRBASKET:
            if self.__now_tok.ttype != AIL_COMMA:
                self.__syntax_error()

            self.__next_tok()  # eat ','

            a = self.__parse_arg_item(True, try_tuple)
            alist.append(a)

        return ast.ArgListAST(alist, self.__now_ln)

    def __parse_type_comment(self, start: str = ':'):
        if self.__now_tok != start and start:
            return

        if start is not None:
            self.__next_tok()

        return self.__parse_cell_or_call_expr()

    def __parse_value_list(self) -> ast.ValueListAST:
        if self.__now_tok.ttype != AIL_IDENTIFIER:
            return self.__syntax_error()

        ida = self.__now_tok.value
        idl = [ida]

        self.__next_tok()

        while self.__now_tok == ',' and self.__now_tok.ttype != AIL_ENTER:
            self.__next_tok()

            if self.__now_tok.ttype != AIL_IDENTIFIER:
                self.__syntax_error()

            idl.append(self.__now_tok.value)

            self.__next_tok()

        return ast.ValueListAST(idl, self.__now_ln)

    def __parse_item_list(self) -> ast.ItemListAST:
        if self.__now_tok.ttype == AIL_MRBASKET:
            return ast.ItemListAST([], self.__now_ln)

        il = []

        while self.__now_tok.ttype != AIL_MRBASKET:
            eitem = self.__parse_binary_expr()

            self.__skip_newlines()

            if eitem is None:
                self.__syntax_error()

            il.append(eitem)

            if self.__now_tok.ttype == AIL_COMMA:
                self.__next_tok()

            self.__skip_newlines()

        return ast.ItemListAST(il, self.__now_ln)

    def __parse_array_expr(self) -> ast.ArrayAST:
        ln = self.__now_ln

        if self.__now_tok.ttype != AIL_MLBASKET:
            self.__syntax_error()

        self.__next_tok()  # eat '['
        self.__skip_newlines()

        if self.__now_tok.ttype == AIL_MRBASKET:
            self.__next_tok()  # eat ']'
            return ast.ArrayAST(ast.ItemListAST([], self.__now_ln), ln)

        items = self.__parse_item_list()

        if self.__now_tok.ttype != AIL_MRBASKET:
            self.__syntax_error()

        self.__next_tok()

        if items is None:
            self.__syntax_error()

        return ast.ArrayAST(items, ln)

    def __parse_map_expr(self) -> ast.MapAST:
        ln = self.__now_ln

        if self.__now_tok.ttype != AIL_LLBASKET:
            self.__syntax_error()

        self.__skip_newlines()
        self.__next_tok()  # eat '{' and NEWLINES
        self.__skip_newlines()

        keys = []
        values = []

        if self.__now_tok.ttype == AIL_LRBASKET:
            self.__next_tok()
            return ast.MapAST(keys, values, ln)

        key = self.__parse_binary_expr(type_comment=False)
        self.__skip_newlines()

        if self.__now_tok.ttype != AIL_COLON:
            self.__syntax_error()

        self.__next_tok(ignore_newline=True)  # eat ':'
        self.__skip_newlines()

        value = self.__parse_binary_expr()
        self.__skip_newlines()

        keys.append(key)
        values.append(value)

        while self.__now_tok.ttype == AIL_COMMA:
            self.__next_tok()
            self.__skip_newlines()

            if self.__now_tok.ttype == AIL_LRBASKET:
                if len(keys) == 0:
                    self.__syntax_error()
                else:
                    break

            key = self.__parse_binary_expr(type_comment=False, do_tuple=False)
            self.__skip_newlines()

            if self.__now_tok.ttype != AIL_COLON:
                self.__syntax_error()
            self.__next_tok()  # eat ':'

            self.__skip_newlines()
            value = self.__parse_binary_expr(type_comment=False, do_tuple=False)
            self.__skip_newlines()

            keys.append(key)
            values.append(value)

        if self.__now_tok.ttype != AIL_LRBASKET:
            self.__syntax_error()

        self.__next_tok()  # eat '}'

        return ast.MapAST(keys, values, ln)

    def __parse_member_access_expr(self,
                                   set_attr=False,
                                   try_=False) -> ast.MemberAccessAST:
        ln = self.__now_ln
        left = self.__parse_cell_or_call_expr()

        if left is None:
            self.__syntax_error()

        if self.__now_tok.ttype != AIL_DOT:
            return left

        rl = []

        while self.__now_tok == '.':
            self.__next_tok()  # eat '.'
            ert = self.__parse_cell_or_call_expr()

            if ert is None or type(ert) not in (
                    ast.SubscriptExprAST, ast.CallExprAST, ast.CellAST):
                self.__syntax_error()

            if isinstance(ert, ast.CellAST) and ert.type != AIL_IDENTIFIER:
                self.__syntax_error()

            rl.append(ert)

        if set_attr and type(rl[-1]) not in (ast.SubscriptExprAST, ast.CellAST):
            if try_:
                return None
            self.__syntax_error()

        return ast.MemberAccessAST(left, rl, ln)

    def __parse_cell_or_call_expr(self) -> ast.SubscriptExprAST:
        # in fact, it is for subscript
        ca = self.__parse_low_cell_expr()

        left = ca

        while self.__now_tok.ttype in (AIL_MLBASKET, AIL_SLBASKET):
            nt = self.__now_tok.ttype
            ln = self.__now_ln

            if nt == AIL_MLBASKET:
                self.__next_tok()  # eat '['
                if self.__now_tok == ']':
                    self.__syntax_error()

                expr = self.__parse_binary_expr()

                if self.__now_tok != ']':
                    self.__syntax_error()
                self.__next_tok()  # eat ']'

                left = ast.SubscriptExprAST(left, expr, ln)

            elif nt == AIL_SLBASKET:
                self.__next_tok()  # eat '('
                if self.__now_tok == ')':
                    argl = ast.ArgListAST([], ln)
                else:
                    argl = self.__parse_arg_list()

                self.__check_as_arg_list(argl)

                self.__next_tok()  # eat ')'

                left = ast.CallExprAST(left, argl, ln)
        return left

    def __parse_low_cell_expr(self) -> ast.ExprAST:
        ln = self.__now_ln

        if self.__now_tok.ttype == AIL_MLBASKET:
            a = self.__parse_array_expr()

            if a is None:
                self.__syntax_error()

            return a
        elif self.__now_tok.ttype == AIL_LLBASKET:
            a = self.__parse_map_expr()

            if a is None:
                self.__syntax_error()

            return a
        elif self.__now_tok == 'fun' or self.__now_tok == 'func':
            ph_lev = self.__parenthesis_level
            self.__parenthesis_level = 0

            expr = self.__parse_func_def_stmt(anonymous_function=True)

            self.__parenthesis_level = ph_lev

            return expr

        if self.__now_tok == '(':
            self.__next_tok()

            if self.__now_tok == ')':
                if self.__peek().ttype != AIL_RARROW:
                    self.__next_tok()  # eat ')'
                    return ast.TupleAST([], False, ln)

            expr_or_param = self.__parse_arg_list(True)

            if self.__now_tok != ')':
                self.__syntax_error()

            self.__next_tok()  # eat ')'

            exp_list = expr_or_param.arg_list

            if self.__now_tok.ttype != AIL_RARROW:
                for exp in exp_list:
                    if exp.star:
                        self.__syntax_error()
                if len(exp_list) == 1:
                    return expr_or_param.arg_list[0].expr
                else:  # maybe a tuple
                    items = [exp.expr for exp in exp_list]
                    return ast.TupleAST(items, False, ln)

            # now it is lambda expression

            has_star = False

            self.__check_as_param_list(exp_list)

            self.__next_tok()  # eat '->'

            if self.__now_tok.ttype == AIL_LLBASKET:
                block = self.__parse_block()
                a = ast.FunctionDefineAST(
                    aconfig.LAMBDA_FUNC_NAME,
                    expr_or_param, block, None, self.__now_ln)
                return a

            expr = self.__parse_binary_expr()
            return_stmt = ast.ReturnStmtAST(expr, expr.ln)
            block = ast.BlockAST([return_stmt], return_stmt.ln, True)

            a = ast.FunctionDefineAST(
                aconfig.LAMBDA_FUNC_NAME, expr_or_param, block, None, expr.ln)
            a.is_lambda = True
            a.lambda_return = expr
            return a

        nt = self.__now_tok

        if self.__now_tok.ttype == AIL_ENTER:
            self.__syntax_error(ln=self.__now_ln - 1)

        elif self.__now_tok.ttype not in (
                AIL_NUMBER, AIL_STRING, AIL_IDENTIFIER, AIL_SUB) or \
                nt in _keywords:
            self.__syntax_error()
        name = nt.value  # it can be sub, string, number or identifier

        self.__next_tok()  # eat NAME

        return ast.CellAST(name, nt.ttype, ln)

    def __parse_unary_expr(self) -> ast.UnaryExprAST:
        # AIL 1.2a3: not support '++' and '--' anymore.
        if self.__now_tok.ttype in (AIL_PLUS_PLUS, AIL_SUB_SUB):
            self.__syntax_error('not support \'++\' and \'--\' anymore')

        if self.__now_tok.ttype in (
                AIL_SUB, AIL_WAVE):
            ln = self.__now_ln
            op = self.__now_tok.value
            self.__next_tok()  # eat op

            right = self.__parse_member_access_expr()

            if right is None:
                self.__syntax_error()

            return ast.UnaryExprAST(op, right, ln)

        return self.__parse_member_access_expr()

    def __parse_power_expr(self) -> ast.PowerExprAST:
        ln = self.__now_ln

        left = self.__parse_unary_expr()

        if left is None:
            self.__syntax_error()

        if self.__now_tok != '**':
            return left

        rl = []

        while self.__now_tok == '**':
            self.__next_tok()
            r = self.__parse_unary_expr()
            if r is None:
                self.__syntax_error()
            rl.append(('**', r))
        return ast.PowerExprAST(left, rl, ln)

    def __parse_mod_expr(self) -> ast.ModExprAST:
        ln = self.__now_ln

        left = self.__parse_power_expr()

        if left is None:
            self.__syntax_error()

        if self.__now_tok != 'mod':
            return left

        rl = []

        while self.__now_tok == 'mod':
            self.__next_tok()
            r = self.__parse_power_expr()
            if r is None:
                self.__syntax_error()
            rl.append(('mod', r))
        return ast.ModExprAST(left, rl, ln)

    def __parse_muit_div_expr(self) -> ast.MuitDivExprAST:
        ln = self.__now_ln

        left = self.__parse_mod_expr()

        if left is None:
            self.__syntax_error()

        if self.__now_tok.ttype not in (AIL_MULT, AIL_DIV):
            return left

        left_op = self.__now_tok.value

        rl = []

        while self.__now_tok.ttype in (AIL_MULT, AIL_DIV):
            r_op = self.__now_tok.value
            self.__next_tok()

            r = self.__parse_mod_expr()
            if r is None:
                self.__syntax_error()

            rl.append((r_op, r))

        return ast.MuitDivExprAST(left_op, left, rl, ln)

    def __parse_bin_xor_expr(self) -> ast.BinXorExprAST:
        ln = self.__now_ln

        left = self.__parse_bit_shift_expr()

        if left is None:
            self.__syntax_error()

        if self.__now_tok != '^':
            return left

        rl = []

        while self.__now_tok == '^':
            self.__next_tok()
            r = self.__parse_bit_shift_expr()
            if r is None:
                self.__syntax_error()
            rl.append(('^', r))
        return ast.BinXorExprAST(left, rl, ln)

    def __parse_binary_expr(
            self, as_stmt: bool = False, do_tuple: bool = False,
            no_assign: bool = False, type_comment: bool = True) -> ast.BitOpExprAST:
        expr = self.__parse_assign_expr(
            do_tuple, no_assign=no_assign, type_comment=type_comment)

        if isinstance(expr, ast.AssignExprAST) and not as_stmt:
            self.__syntax_error('cannot assign in a expression')

        return expr

    def __parse_bin_op_expr(self) -> ast.BitOpExprAST:
        ln = self.__now_ln

        left = self.__parse_bin_xor_expr()

        if left is None:
            self.__syntax_error()

        if self.__now_tok.ttype not in (AIL_BIN_OR, AIL_BIN_AND):
            return left

        left_op = self.__now_tok.value

        rl = []

        while self.__now_tok.ttype in (AIL_BIN_OR, AIL_BIN_AND):
            r_op = self.__now_tok.value
            self.__next_tok()

            r = self.__parse_bin_xor_expr()
            if r is None:
                self.__syntax_error()

            rl.append((r_op, r))

        return ast.BitOpExprAST(left_op, left, rl, ln)

    def __parse_tuple_expr(self, do_tuple: bool = False) -> ast.TupleAST:
        ln = self.__now_ln
        expr = self.__parse_test_expr()

        # check tuple

        if self.__now_tok.ttype != AIL_COMMA or not do_tuple:
            return expr

        items = [expr]

        while self.__now_tok.ttype == AIL_COMMA:
            self.__next_tok()
            item = self.__parse_test_expr()
            items.append(item)

        return ast.TupleAST(items, False, ln)

    def __parse_bit_shift_expr(self) -> ast.BitShiftExprAST:
        ln = self.__now_ln

        left = self.__parse_add_sub_expr()

        if left is None:
            self.__syntax_error()

        if self.__now_tok.ttype not in (AIL_LSHIFT, AIL_RSHIFT):
            return left

        left_op = self.__now_tok.value

        rl = []

        while self.__now_tok.ttype in (AIL_LSHIFT, AIL_RSHIFT):
            r_op = self.__now_tok.value
            self.__next_tok()

            r = self.__parse_add_sub_expr()
            if r is None:
                self.__syntax_error()

            rl.append((r_op, r))

        return ast.BitShiftExprAST(left_op, left, rl, ln)

    def __parse_add_sub_expr(self) -> ast.AddSubExprAST:
        ln = self.__now_ln

        left = self.__parse_muit_div_expr()

        if left is None:
            self.__syntax_error()

        if self.__now_tok.ttype not in (AIL_PLUS, AIL_SUB):
            return left

        left_op = self.__now_tok.value

        rl = []

        while self.__now_tok.ttype in (AIL_PLUS, AIL_SUB):
            r_op = self.__now_tok.value
            self.__next_tok()

            r = self.__parse_muit_div_expr()
            if r is None:
                self.__syntax_error()

            rl.append((r_op, r))

        return ast.AddSubExprAST(left_op, left, rl, ln)

    def __parse_print_expr(self) -> ast.PrintStmtAST:
        ln = self.__now_ln

        self.__next_tok()  # eat 'PRINT'

        exp = self.__parse_binary_expr()

        if exp is None:
            self.__syntax_error()

        el = [exp]

        sep = ','
        if aconfig.OLD_PRINT:
            sep = ';'

        while self.__now_tok == sep:
            self.__next_tok()
            e = self.__parse_binary_expr()

            if e is None:
                self.__syntax_error()

            el.append(e)

        self.__expect_newline()

        return ast.PrintStmtAST(el, ln)

    def __parse_input_expr(self) -> ast.InputStmtAST:
        ln = self.__now_ln

        self.__next_tok()  # eat 'INPUT'

        msg = self.__parse_binary_expr()

        if msg is None:
            self.__syntax_error()

        if self.__now_tok != ',':
            return ast.InputStmtAST(
                msg, ast.ValueListAST([], self.__now_ln), self.__now_ln)

        if self.__next_tok().ttype == AIL_IDENTIFIER:
            vl = self.__parse_value_list()
        else:
            vl = ast.ValueListAST([], self.__now_ln)

        self.__expect_newline()

        return ast.InputStmtAST(msg, vl, ln)

    def __parse_assign_expr(self,
                            do_tuple: bool = False,
                            no_assign: bool = False,
                            type_comment: bool = True) -> ast.AssignExprAST:
        ln = self.__now_ln
        left = self.__parse_tuple_expr(do_tuple)

        if type_comment:
            self.__parse_type_comment()

        state = self.get_state()

        if no_assign:
            self.set_state(state)
            return left

        if left is None:
            self.__syntax_error()

        ttype = self.__now_tok.ttype

        if ttype != AIL_ASSI and \
                (ttype < AIL_INP_PLUS or ttype > AIL_INP_BIN_AND) and \
                ttype != AIL_INP_POW:
            self.set_state(state)
            return left

        # check left is valid or not
        if type(left) not in (ast.MemberAccessAST,
                              ast.CellAST, ast.SubscriptExprAST,
                              ast.TupleAST):
            self.__syntax_error(ln=left.ln)

        if isinstance(left, ast.TupleAST):
            for elt in left.items:
                if type(elt) not in (
                        ast.MemberAccessAST, ast.CellAST, ast.SubscriptExprAST):
                    self.__syntax_error()

        # check cell is valid or not
        if isinstance(left, ast.CellAST):
            if left.value in _literal_names:
                self.__syntax_error('cannot assign to %s' % left.value, left.ln)
            elif left.type in (AIL_NUMBER, AIL_STRING):
                self.__syntax_error('cannot assign to literal', left.ln)

        self.__next_tok()
        r = self.__parse_binary_expr(do_tuple=do_tuple)
        if r is None:
            self.__syntax_error()

        aug_assign = False

        if ttype in range(AIL_INP_PLUS, AIL_INP_BIN_AND + 1) or \
                ttype == AIL_INP_POW:
            r = self.__convert_inplace_assign_expr_for_right(
                left, r, ttype, self.__now_ln)
            aug_assign = True

        return ast.AssignExprAST(left, r, ln, aug_assign)

    def __convert_inplace_assign_expr_for_right(
            self, left, right, ttype, ln) -> ast.ExprAST:
        op_str, op_ast, need_op_str = _inplace_op_dict.get(ttype)

        if need_op_str:
            arg = (op_str, left, [[op_str, right]], ln)
        else:
            arg = (left, [[op_str, right]], ln)

        return op_ast(*arg)

    def __parse_assign_expr0(self) -> ast.DefineExprAST:
        n = self.__now_tok.value
        self.__next_tok()
        self.__next_tok()

        v = self.__parse_binary_expr()

        return ast.DefineExprAST(n, v, self.__now_ln)

    def __parse_comp_test_expr(self) -> ast.CmpTestAST:
        ln = self.__now_ln

        left = self.__parse_bin_op_expr()

        if left is None:
            self.__syntax_error()

        if self.__now_tok.ttype not in _cmp_op:
            return left

        rl = []

        while self.__now_tok.ttype in _cmp_op:
            now_op = self.__now_tok.value
            self.__next_tok()  # eat cmp op
            r = self.__parse_bin_op_expr()

            if r is None:
                self.__syntax_error()

            rl.append((now_op, r))

        return ast.CmpTestAST(left, rl, ln)

    def __parse_not_test_expr(self) -> ast.NotTestAST:
        if self.__now_tok == 'not':
            self.__next_tok()  # eat 'not'
            expr = self.__parse_comp_test_expr()

            return ast.NotTestAST(expr, self.__now_ln)
        return self.__parse_comp_test_expr()

    def __parse_and_test_expr(self) -> ast.AndTestAST:
        left = self.__parse_not_test_expr()

        if left is None:
            self.__syntax_error()

        if self.__now_tok != 'and':
            return left

        rl = []

        while self.__now_tok == 'and':
            self.__next_tok()  # eat 'and'
            r = self.__parse_not_test_expr()

            if r is None:
                self.__syntax_error()

            rl.append(r)

        return ast.AndTestAST(left, rl, self.__now_ln)

    def __parse_or_test_expr(self) -> ast.OrTestAST:
        left = self.__parse_and_test_expr()

        if left is None:
            self.__syntax_error()

        if self.__now_tok != 'or':
            return left

        rl = []

        while self.__now_tok == 'or':
            self.__next_tok()  # eat 'or'
            r = self.__parse_and_test_expr()

            if r is None:
                self.__syntax_error()

            rl.append(r)

        return ast.OrTestAST(left, rl, self.__now_ln)

    def __parse_test_expr(self, as_stmt: bool = True) -> ast.TestExprAST:
        t = self.__parse_or_test_expr()

        if type(t) not in (
                ast.AndTestAST, ast.OrTestAST, ast.NotTestAST, ast.CmpTestAST):
            return t

        if t is None:
            self.__syntax_error()

        return ast.TestExprAST(t, self.__now_ln)

    def __parse_new_else_elif_block(self, test: ast.TestExprAST,
                                    if_block: ast.BlockAST,
                                    else_block: ast.BlockAST,
                                    ln: int) -> ast.IfStmtAST:
        elif_list = []

        self.__skip_newlines()

        if self.__now_tok.value not in ('else', 'elif'):
            return ast.IfStmtAST(test, if_block, elif_list, else_block, ln)

        while self.__now_tok.ttype != AIL_EOF:
            self.__skip_newlines()
            if self.__now_tok == 'else':
                self.__skip_newlines()
                self.__next_tok(ignore_newline=True)  # eat 'else'
                else_block = self.__parse_block()
                if else_block is None:
                    self.__syntax_error()
                break

            elif self.__now_tok == 'elif':
                self.__next_tok()  # eat 'elif'
                elif_test = self.__parse_test_expr()

                if elif_test is None:
                    self.__syntax_error()

                elif_block = self.__parse_block()

                if elif_block is None:
                    self.__syntax_error()

                elif_list.append(
                    ast.IfStmtAST(elif_test, elif_block, [], None, ln))

            else:
                break

        return ast.IfStmtAST(test, if_block, elif_list, else_block, ln)

    def __parse_if_else_expr0(self) -> ast.IfStmtAST:
        ln = self.__now_ln
        self.__next_tok()  # eat 'if'

        if_test = self.__parse_test_expr()

        if if_test is None:
            self.__syntax_error()

        is_new_block = self.__now_tok.ttype == AIL_LLBASKET

        if self.__now_tok != 'then' and not is_new_block:
            self.__syntax_error()

        if not is_new_block:
            self.__next_tok()  # eat 'then'

        if_block = self.__parse_block(for_if_else=True)
        else_block = ast.BlockAST([], self.__now_ln)
        elif_list = []

        if is_new_block:
            return self.__parse_new_else_elif_block(if_test, if_block, else_block, ln)

        while self.__now_tok.ttype != AIL_EOF:
            if self.__now_tok == 'else':
                self.__next_tok()  # eat 'else'
                else_block = self.__parse_block(for_if_else=True)

                if else_block is None:
                    self.__syntax_error()

                if self.__now_tok == 'endif':
                    self.__next_tok()  # eat 'endif'
                    return ast.IfStmtAST(
                        if_test, if_block, elif_list, else_block, ln)
                else:
                    self.__syntax_error()

            elif self.__now_tok == 'elif':
                self.__next_tok()  # eat 'elif'
                elif_test = self.__parse_test_expr()

                if elif_test is None:
                    self.__syntax_error()

                elif_block = self.__parse_block(for_if_else=True)

                if elif_block is None:
                    self.__syntax_error()

                elif_list.append(
                    ast.IfStmtAST(elif_test, elif_block, [], None, ln))

            elif self.__now_tok == 'endif':
                self.__next_tok()  # eat 'endif'
                self.__expect_newline()
                return ast.IfStmtAST(
                    if_test, if_block, elif_list, else_block, ln)

            else:
                self.__syntax_error()

    def __parse_if_else_expr(self) -> ast.IfStmtAST:
        base_tree = self.__parse_if_else_expr0()

        if len(base_tree.elif_list) == 0:
            return base_tree

        elif_list = base_tree.elif_list

        last_if_else_block = elif_list.pop()
        last_if_else_block.else_block = base_tree.else_block
        base_tree.else_block = None

        for elif_block in elif_list[::-1]:
            elif_block.else_block = ast.BlockAST([last_if_else_block], self.__now_ln)
            last_if_else_block = elif_block

        base_tree.else_block = ast.BlockAST([last_if_else_block], self.__now_ln)
        base_tree.elif_list = []

        return base_tree

    def __parse_while_expr(self) -> ast.WhileStmtAST:
        ln = self.__now_ln
        self.__next_tok()  # eat 'while'

        test = self.__parse_test_expr()

        if test is None:
            self.__syntax_error()

        block = self.__parse_block('then', 'wend',
                                   'while block should starts with \'then\'',
                                   "while block should ends with 'wend'")

        if block is None:
            self.__syntax_error()

        if not block.new:
            self.__expect_newline()

        return ast.WhileStmtAST(test, block, ln)

    def __parse_assign_expr_list(self) -> ast.AssignExprListAST:
        f = self.__parse_binary_expr(True)
        if not isinstance(f, ast.AssignExprAST):
            self.__syntax_error()

        el = [f]

        while self.__now_tok == ',':
            self.__next_tok()  # eat ','
            e = self.__parse_binary_expr(True)
            if not isinstance(e, ast.AssignExprAST):
                self.__syntax_error()
            el.append(e)

        return ast.AssignExprListAST(el, self.__now_ln)

    def __parse_binary_expr_list(self) -> ast.BinaryExprListAST:
        f = self.__parse_binary_expr(True)

        el = [f]

        while self.__now_tok == ',':
            self.__next_tok()  # eat ','
            e = self.__parse_binary_expr(True)
            if e is None:
                self.__syntax_error()
            el.append(e)

        return ast.BinaryExprListAST(el, self.__now_ln)

    def __parse_new_for_stmt(self, from_classic=False) -> ast.ForStmtAST:
        if not from_classic and self.__now_tok != 'for':
            self.__syntax_error()

        ln = self.__now_ln

        if not from_classic:
            self.__next_tok()  # eat 'for'

        init: ast.AssignExprListAST = ast.AssignExprListAST([], ln)
        test = None
        update: ast.BinaryExprListAST = ast.AssignExprListAST([], ln)

        if self.__now_tok == '{' or self.__now_tok == 'then':
            body = self.__parse_block()
            return ast.WhileStmtAST(ast.CellAST('1', AIL_NUMBER, ln), body, ln)

        if self.__now_tok != ';':
            init = self.__parse_binary_expr_list()
            if len(init.expr_list) == 1 and \
                    (self.__now_tok == '{' or self.__now_tok == 'then'):
                body = self.__parse_block()
                return ast.WhileStmtAST(init.expr_list[0], body, ln)
            # compatible with classic for.
            init = ast.AssignExprListAST(init.expr_list, ln)

            if self.__now_tok != ';':
                self.__syntax_error()
            self.__next_tok()  # eat ';'
        else:
            self.__next_tok()  # eat ';'

        if self.__now_tok != ';':
            test = self.__parse_test_expr()
            if self.__now_tok != ';':
                self.__syntax_error()

            self.__next_tok()
        else:
            self.__next_tok()

        if self.__now_tok != '{' and self.__now_tok != 'then':
            update = self.__parse_binary_expr_list()

        body = self.__parse_block()

        return ast.ForStmtAST(init, test, update, body, ln)

    def __parse_for_expr(self) -> ast.ForStmtAST:
        if self.__now_tok != 'for':
            self.__syntax_error()

        if self.__next_tok() != '(':
            return self.__parse_new_for_stmt(True)

        self.__next_tok()  # eat '('

        mt = [None, None, None]
        pt = (self.__parse_assign_expr_list,
              self.__parse_test_expr,
              self.__parse_binary_expr_list)
        ct = (ast.AssignExprListAST,
              None,
              ast.BinaryExprListAST)
        dt = (';', ';', ')')

        for i in range(3):
            if self.__now_tok == dt[i]:
                if ct[i] is not None:
                    mt[i] = ct[i]([], self.__now_ln)
                self.__next_tok()
            else:
                mt[i] = pt[i]()

                if self.__now_tok != dt[i]:
                    self.__syntax_error()
                self.__next_tok()

        initl, test, binl = mt

        forb = self.__parse_block()
        if not forb.new:
            self.__expect_newline()

        return ast.ForStmtAST(initl, test, binl, forb, self.__now_ln)

    def __parse_do_loop_expr(self) -> ast.DoLoopStmtAST:
        ln = self.__now_ln

        new_block_style = False

        if self.__peek(1).ttype != AIL_LLBASKET:
            block = self.__parse_block('do', 'loop',
                                       'do loop statement should starts with \'do\'',
                                       "do loop statement should ends with 'until'")
        else:
            new_block_style = True
            self.__next_tok()  # eat 'do'
            block = self.__parse_block()

        if block is None:
            self.__syntax_error()

        if new_block_style:
            if self.__now_tok != 'loop':
                self.__syntax_error()
            self.__next_tok()  # eat 'loop'

        if self.__now_tok != 'until':
            self.__syntax_error()

        self.__next_tok()  # eat until

        test = self.__parse_test_expr()

        self.__expect_newline()

        return ast.DoLoopStmtAST(test, block, ln)

    def __parse_struct_def_stmt(self) -> ast.StructDefineAST:
        if self.__now_tok != 'struct':
            self.__syntax_error()

        ln = self.__now_ln

        self.__next_tok()  # eat 'struct'

        if self.__now_tok.ttype != AIL_IDENTIFIER:
            self.__syntax_error()

        name = self.__now_tok.value
        self.__next_tok()  # eat name

        new_block_style = False

        start_tok = 'is'
        end_tok = 'end'

        if self.__now_tok.ttype == AIL_LLBASKET:
            start_tok = '{'
            end_tok = '}'
            new_block_style = True

        if self.__now_tok != start_tok or \
                self.__next_tok().ttype != AIL_ENTER:
            if not new_block_style:
                self.__syntax_error()

        if self.__now_tok.ttype == AIL_ENTER:
            self.__next_tok()  # eat ENTER

        vl = []
        pl = []

        while self.__now_tok.ttype == AIL_ENTER:
            self.__next_tok()

        while self.__now_tok != end_tok:
            if self.__now_tok.ttype != AIL_IDENTIFIER:
                self.__syntax_error()

            if self.__now_tok == 'protected':
                nt = self.__next_tok()
                if nt.ttype != AIL_IDENTIFIER:
                    self.__syntax_error()
                pl.append(nt.value)

            vl.append(self.__now_tok.value)
            self.__next_tok()  # eat NAME

            self.__parse_type_comment()

            if self.__now_tok.ttype != AIL_ENTER:
                self.__syntax_error()

            self.__next_tok()  # eat ENTER

            while self.__now_tok.ttype == AIL_ENTER:
                self.__next_tok()

        if self.__now_tok != end_tok:
            self.__syntax_error()

        self.__next_tok()  # eat end_tok

        return ast.StructDefineAST(name, vl, pl, ln)

    def __parse_doc_string_object(self):
        if self.__now_tok.ttype != AIL_DOC_STRING:
            self.__syntax_error()
        doc_string = self.__now_tok.value

        ln = self.__now_ln

        self.__next_tok(convert_semi=False)  # eat DOC STRING

        if self.__pyc_mode:
            return ast.PyCodeBlock(doc_string, ln)

        nt = self.__now_tok
        if nt == 'class':
            return self.__parse_class_def_stmt(doc_string=doc_string)
        elif nt == 'fun' or nt == 'func':
            return self.__parse_func_def_stmt(doc_string=doc_string)
        elif nt == '@':
            return self.__parse_func_def_with_decorator_stmt(doc_string=doc_string)
        else:
            self.__syntax_error('invalid definition under doc string')

    def __parse_func_def_with_decorator_stmt(self,
                                             parsed: list = None,
                                             doc_string='') -> ast.FunctionDefineAST:
        ln = self.__now_ln
        self.__next_tok()  # eat '@'

        if parsed is None:
            parsed = list()

        decorator = self.__parse_member_access_expr()

        parsed.append(decorator)

        if self.__now_tok.ttype != AIL_ENTER:
            self.__syntax_error()

        self.__next_tok()  # eat enter

        if self.__now_tok == '@':
            return self.__parse_func_def_with_decorator_stmt(parsed)
        elif self.__now_tok == 'fun' or self.__now_tok == 'func':
            func = self.__parse_func_def_stmt(doc_string=doc_string)
            func.decorator.extend(parsed)

            return func
        else:
            self.__syntax_error()

    def __parse_class_bases(self) -> list:
        bases = []

        first = self.__parse_binary_expr()
        bases.append(first)

        while self.__now_tok == ',':
            self.__next_tok()
            sub = self.__parse_binary_expr(do_tuple=False)
            bases.append(sub)

        return bases

    def __parse_class_def_stmt(self, doc_string='') -> ast.ClassDefineAST:
        """
        the class definition actually a function definition.
        """

        ln = self.__now_ln
        self.__next_tok()  # eat 'class'

        if self.__now_tok.ttype != AIL_IDENTIFIER:
            self.__syntax_error()

        class_name = self.__now_tok.value
        bases = []
        meta = None

        self.__next_tok()  # eat NAME

        if self.__now_tok.ttype == AIL_COLON:
            self.__next_tok()  # eat ':'
            meta = self.__parse_binary_expr(do_tuple=False)

        if self.__now_tok == 'extends':
            self.__next_tok()
            bases = self.__parse_class_bases()

        body = self.__parse_block('is', 'end',
                                  start_msg=
                                  'class body should starts with \'is\' or \':\'',
                                  class_body=True)

        instance_property = []

        # check define
        stmts = body.stmts
        new_stmts = []
        properties = []

        for i, stmt in enumerate(stmts):
            if isinstance(stmt, ast.StaticAssign):
                stmt: ast.StaticAssign
                if isinstance(stmt.assign, ast.AssignExprAST):
                    new_stmts.append(stmt.assign)
                elif isinstance(stmt.assign, ast.CellAST):
                    pass
                elif isinstance(stmt.assign, ast.FunctionDefineAST):
                    func: ast.FunctionDefineAST = stmt.assign
                    func.decorator.append(ast.CellAST('classmethod', AIL_IDENTIFIER, func.ln))
                    new_stmts.append(func)
                else:
                    self.__syntax_error(ln=stmt.ln)

            elif isinstance(stmt, ast.AssignModifier):
                stmt: ast.AssignModifier
                assign = stmt.assign

                if isinstance(assign, ast.CellAST):
                    continue
                elif isinstance(assign, ast.AssignExprAST):
                    left = assign.left
                else:
                    self.__syntax_error(ln=assign.ln)

                if isinstance(left, ast.CellAST):
                    self.__property_rename(left, stmt.context)

                elif isinstance(left, ast.TupleAST):
                    left: ast.TupleAST

                    for n in left.items:
                        self.__property_rename(n, stmt.context)
                else:
                    self.__syntax_error('illegal class property definition', left.ln)

                if stmt.static:
                    new_stmts.append(assign)
                else:
                    instance_property.append(assign)

            elif isinstance(stmt, ast.AssignExprAST):
                instance_property.append(stmt)

            elif isinstance(stmt, ast.PropertyDefine):
                stmt: ast.PropertyDefine
                func = stmt.func
                if stmt.action == 'get':
                    properties.append(func.name)
                    func.decorator.append(ast.CellAST('property', AIL_IDENTIFIER, func.ln))
                elif stmt.action == 'set':
                    if func.name not in properties:
                        self.__syntax_error('property \'%s\' must have a getter' % func.name, func.ln)
                    func.decorator.append(
                        ast.MemberAccessAST(ast.CellAST(func.name, AIL_IDENTIFIER, func.ln),
                                            [ast.CellAST('setter', AIL_IDENTIFIER, func.ln)], func.ln)
                    )

                new_stmts.append(func)
            else:
                new_stmts.append(stmt)

        body.stmts = new_stmts
        stmts = new_stmts

        # setup instance property
        if instance_property:
            # find __init__
            init = None
            block = []
            for stmt in stmts:
                if isinstance(stmt, ast.FunctionDefineAST):
                    stmt: ast.FunctionDefineAST
                    if stmt.bindto is not None:
                        self.__syntax_error('illegal method definition', stmt.ln)

                    if stmt.name == '__init__':
                        init = stmt
                        block = stmt.block.stmts
                        break
            else:
                init = ast.FunctionDefineAST(
                    '__init__',
                    ast.ArgListAST([ast.ArgItemAST(ast.CellAST(
                        'self', AIL_IDENTIFIER, ln), False, ln)], ln),
                    ast.BlockAST(block, ln), None, ln)
                stmts.insert(0, init)

            for assign in instance_property:
                if isinstance(assign, ast.AssignExprAST):
                    left = assign.left
                else:
                    self.__syntax_error(ln=assign.ln)

                if isinstance(left, ast.CellAST):
                    left: ast.CellAST
                    access = ast.MemberAccessAST(
                        ast.CellAST('self', AIL_IDENTIFIER, ln), [left], ln)
                    assign.left = access

                    block.insert(0, assign)
                elif isinstance(left, ast.TupleAST):
                    for i, n in enumerate(left.items):
                        access = ast.MemberAccessAST(
                            ast.CellAST('self', AIL_IDENTIFIER, ln), [n], ln)
                        left.items[i] = access

                    block.insert(0, assign)

        body.stmts = body.stmts[::-1]

        func = ast.FunctionDefineAST(
            class_name, ast.ArgListAST([], ln), body, None, ln)

        return ast.ClassDefineAST(class_name, func, bases, meta, ln, doc_string)

    def __property_rename(self, left, context: str):
        if isinstance(left, ast.CellAST) and left.type == AIL_IDENTIFIER:
            if context == 'private':
                left: ast.CellAST
                name: str = left.value

                if name.startswith('__'):
                    pass  # already a 'private' property
                elif name.startswith('_'):
                    self.__syntax_error('this property is \'protected\'', left.ln)
                else:
                    left.value = '__%s' % name

            elif context == 'protected':
                left: ast.CellAST
                name: str = left.value

                if name.startswith('__'):
                    self.__syntax_error('this property is \'private\'', left.ln)
                elif name.startswith('_'):
                    pass  # already a 'protected' property
                else:
                    left.value = '_%s' % name
        else:
            self.__syntax_error(
                'only class property can use \'protected\' or \'private\' definition',
                left.ln)

    def __parse_func_def_stmt(
            self, anonymous_function: bool = False,
            doc_string='', with_bound_to: bool = True) -> ast.FunctionDefineAST:
        ln = self.__now_ln
        self.__next_tok()  # eat 'fun'

        bindto = None
        bindto_tok_line = 0

        if self.__now_tok == '(' and not anonymous_function:
            if self.__next_tok().ttype != AIL_IDENTIFIER:
                self.__syntax_error()
            bindto = self.__now_tok.value
            bindto_tok_line = self.__now_ln

            if self.__next_tok() != ')':
                self.__syntax_error()
            self.__next_tok()

        if bindto is not None and not with_bound_to:
            self.__syntax_error('this function can not be bound', bindto_tok_line)

        if anonymous_function:
            name = aconfig.ANONYMOUS_FUNC_NAME
        else:
            if self.__now_tok.ttype != AIL_IDENTIFIER:
                self.__syntax_error()

            name = self.__now_tok.value

            self.__next_tok()  # eat NAME

        if self.__now_tok != '(':
            self.__syntax_error()

        self.__next_tok()  # eat '('

        if self.__now_tok == ')':
            arg_list = ast.ArgListAST([], self.__now_ln)  # empty arglist

            self.__next_tok()  # eat ')'
        else:
            arg_list = self.__parse_param_list()

        self.__parse_type_comment()

        self.__skip_newlines()

        self.__level += 1

        # for new function syntax (':' instead of 'is')
        # if self.__now_tok.ttype == AIL_COLON:
        #     self.__now_tok.ttype = AIL_IDENTIFIER
        #     self.__now_tok.value = 'is'
        # not longer supported at 1.2 alpha 4 - 2021 6 3

        block = self.__parse_block('is', 'end',
                                   start_msg=
                                   'function body should starts with \'is\' or \':\'')

        self.__level -= 1

        return ast.FunctionDefineAST(name, arg_list, block, bindto, ln, doc_string)

    def __parse_continue_stmt(self) -> ast.ContinueStmtAST:
        if self.__now_tok != 'continue':
            self.__syntax_error()

        self.__next_tok()  # eat 'continue'

        self.__expect_newline()

        return ast.ContinueStmtAST(self.__now_ln)

    def __parse_break_stmt(self) -> ast.BreakStmtAST:
        if self.__now_tok != 'break':
            self.__syntax_error()

        self.__next_tok()  # eat 'break'

        self.__expect_newline()

        return ast.BreakStmtAST(self.__now_ln)

    def __parse_global_stmt(self) -> ast.GlobalStmtAST:
        if self.__now_tok != 'global':
            self.__syntax_error()

        ln = self.__now_ln

        self.__next_tok()  # eat 'global'

        if self.__now_tok.ttype != AIL_IDENTIFIER:
            self.__syntax_error()

        name = self.__now_tok.value

        self.__next_tok()  # eat name

        self.__expect_newline()

        return ast.GlobalStmtAST(name, ln)

    def __parse_nonlocal_stmt(self) -> ast.NonlocalStmtAST:
        if self.__now_tok != 'nonlocal':
            self.__syntax_error()

        ln = self.__now_ln

        self.__next_tok()  # eat 'nonlocal'

        if self.__now_tok.ttype != AIL_IDENTIFIER:
            self.__syntax_error()

        name = self.__now_tok.value

        self.__next_tok()  # eat name

        self.__expect_newline()

        return ast.NonlocalStmtAST(name, ln)

    def __parse_return_stmt(self) -> ast.ReturnStmtAST:
        if self.__now_tok != 'return':
            self.__syntax_error()

        self.__next_tok()  # eat 'return'

        if self.__now_tok.ttype == AIL_ENTER:
            expr = ast.CellAST('null', AIL_IDENTIFIER, self.__now_ln)
        else:
            expr = self.__parse_binary_expr(do_tuple=True)

        if expr is None:
            self.__syntax_error()

        self.__expect_newline()

        return ast.ReturnStmtAST(expr, self.__now_ln)

    def __parse_throw_expr(self) -> ast.ThrowStmtAST:
        if self.__now_tok != 'throw':
            self.__syntax_error()

        ln = self.__now_ln

        self.__next_tok()  # eat 'throw'

        if self.__now_tok.ttype == AIL_ENTER:
            return ast.ThrowStmtAST(None, ln)

        expr = self.__parse_binary_expr()

        if expr is None or \
                self.__now_tok.ttype != AIL_ENTER:
            self.__syntax_error()

        self.__expect_newline()

        return ast.ThrowStmtAST(expr, ln)

    def __parse_assert_expr(self) -> ast.AssertStmtAST:
        if self.__now_tok != 'assert':
            self.__syntax_error()

        self.__next_tok()  # eat 'assert'

        expr = self.__parse_test_expr()

        if expr is None or \
                self.__now_tok.ttype != AIL_ENTER:
            self.__syntax_error()

        self.__expect_newline()

        return ast.AssertStmtAST(expr, self.__now_ln)

    def __parse_import_stmt(self) -> ast.ImportStmtAST:
        if self.__now_tok != 'import':
            self.__syntax_error()

        ln = self.__now_ln
        alias = None
        is_dir = False

        self.__next_tok()  # eat 'import'

        if self.__now_tok.ttype == AIL_IDENTIFIER:
            alias = self.__now_tok.value
            self.__next_tok()  # eat name

        if self.__now_tok.ttype != AIL_STRING:
            self.__syntax_error()

        path = self.__now_tok.value
        path = path.replace('\\', '/')
        directory, target = split(path)

        if target == '':
            directory, target = split(directory)  # directory name
            is_dir = True

        if target == '':
            self.__syntax_error('Cannot import a package in current directory.')

        if is_dir:
            if directory == '':
                directory = '.'

            path = '/'.join((directory, target, aconfig.PACKAGE_INIT_FILENAME))
            alias = target

        if alias is None:
            name = target
        else:
            name = alias

        self.__next_tok()  # eat path

        if self.__now_tok.ttype != AIL_SLBASKET:
            return ast.ImportStmtAST(path, name, ln)

        self.__next_tok()  # eat '('

        members = []

        while self.__now_tok.ttype != AIL_SRBASKET:
            if self.__now_tok.ttype != AIL_IDENTIFIER:
                self.__syntax_error()
            members.append(self.__now_tok.value)
            self.__next_tok()  # eat name

            if self.__now_tok.ttype == AIL_SRBASKET:
                break

            if self.__now_tok.ttype != AIL_COMMA:
                self.__syntax_error()
            self.__next_tok()  # eat ','

        self.__next_tok()  # eat ')'

        self.__expect_newline()

        return ast.ImportStmtAST(path, name, ln, members)

    def __parse_load_stmt(self) -> ast.LoadStmtAST:
        if self.__now_tok != 'load':
            self.__syntax_error()

        ln = self.__now_ln

        self.__next_tok()  # eat 'load'

        if self.__now_tok.ttype != AIL_STRING:
            self.__syntax_error()

        name = self.__now_tok.value

        self.__next_tok()  # eat path

        self.__expect_newline()

        return ast.LoadStmtAST(name, ln)

    def __parse_new_catch_finally_body(self,
                                       try_block: ast.BlockAST) -> ast.TryCatchStmtAST:

        has_catch_block = True
        ln = self.__now_ln

        self.__skip_newlines()

        if self.__now_tok != 'catch':
            has_catch_block = False
            catch_block = ast.BlockAST([], self.__now_ln)
            cname = None
        else:
            self.__next_tok()  # eat 'catch'

            if self.__now_tok.ttype != AIL_IDENTIFIER:
                self.__syntax_error('require name')

            cname = self.__now_tok.value

            self.__next_tok()  # eat NAME

            catch_block = self.__parse_block()
            if catch_block is None:
                self.__syntax_error()

        self.__skip_newlines()

        if self.__now_tok != 'finally':
            if not has_catch_block:
                self.__syntax_error()

            return ast.TryCatchStmtAST(
                try_block, catch_block,
                ast.BlockAST([], self.__now_ln), cname, ln)

        self.__next_tok()  # eat 'finally'

        finally_block = self.__parse_block()

        return ast.TryCatchStmtAST(
            try_block, catch_block, finally_block, cname, ln)

    def __parse_static_assign(self) -> ast.StaticAssign:
        ln = self.__now_ln

        self.__next_tok()  # eat 'static'

        if self.__now_tok == 'func' or self.__now_tok == 'fun':
            assign = self.__parse_func_def_stmt()
        else:
            assign = self.__parse_assign_expr(True)

        return ast.StaticAssign(assign, ln)

    def __parse_modified_assign(self) -> ast.AssignModifier:
        ln = self.__now_ln

        context = self.__now_tok.value

        self.__next_tok()

        static = False

        if self.__now_tok == 'static':
            s = self.__parse_static_assign()
            assign = s.assign
            static = True
        else:
            assign = self.__parse_assign_expr(True)

        return ast.AssignModifier(assign, static, context, ln)

    def __parse_property_define(self) -> ast.PropertyDefine:
        if self.__now_tok not in ('get', 'set'):
            self.__syntax_error()

        ln = self.__now_ln

        action = self.__now_tok.value
        self.__next_tok()

        if self.__now_tok.ttype != AIL_IDENTIFIER:
            self.__syntax_error()

        name = self.__now_tok.value
        func = self.__parse_func_def_stmt(True)
        func.name = name

        return ast.PropertyDefine(func, action, ln)

    def __parse_try_catch_expr(self) -> ast.TryCatchStmtAST:
        if self.__now_tok != 'try':
            self.__syntax_error()

        new_block_style = False

        if self.__peek(1).ttype == AIL_LLBASKET:
            new_block_style = True
            self.__next_tok()  # eat '{'

        try_b = self.__parse_block(start='try', end='catch')

        self.__skip_newlines()

        if new_block_style:
            return self.__parse_new_catch_finally_body(try_b)

        if new_block_style:
            self.__next_tok()  # eat 'catch'

        if try_b is None or self.__now_tok.ttype != AIL_IDENTIFIER:
            self.__syntax_error()

        cname = self.__now_tok.value

        self.__next_tok()  # eat NAME

        if self.__now_tok != 'then' or self.__next_tok().ttype != AIL_ENTER:
            self.__syntax_error()

        self.__next_tok()  # eat ENTER

        catch_sl = []
        finally_sl = []

        now = catch_sl
        in_finally = False

        while self.__now_tok != 'end':
            if self.__now_tok == 'finally':
                if in_finally:
                    self.__syntax_error()

                in_finally = True

                if self.__next_tok().ttype != AIL_ENTER:
                    self.__syntax_error()

                self.__next_tok()  # eat ENTER

                now = finally_sl

            if self.__now_tok == 'end':
                break

            s = self.__parse_stmt()

            if isinstance(s, ast.NullLineAST):
                pass
            elif isinstance(s, ast.EOFAST):
                self.__syntax_error("try statement should ends with 'end'")
            else:
                now.append(s)

        cab = ast.BlockAST(catch_sl, self.__now_ln)
        fnb = ast.BlockAST(finally_sl, self.__now_ln)

        self.__next_tok()  # eat 'end'

        self.__expect_newline()

        return ast.TryCatchStmtAST(try_b, cab, fnb, cname, self.__now_ln)

    def __parse_special_method(self) -> ast.FunctionDefineAST:
        alias = self.__now_tok.value

        func = self.__parse_func_def_stmt(True)
        func.name = _special_method_map[alias]

        return func

    def __parse_stmt(self, limit: tuple = (), class_body: bool = False) -> ast.ExprAST:
        nt = self.__now_tok

        if nt == 'print':
            a = self.__parse_print_expr()

        elif nt == 'input':
            a = self.__parse_input_expr()

        elif nt == 'if':
            a = self.__parse_if_else_expr()

        elif nt == 'while':
            a = self.__parse_while_expr()

        elif nt == 'for':
            a = self.__parse_for_expr()

        elif nt == 'do':
            a = self.__parse_do_loop_expr()

        elif nt == 'continue':
            a = self.__parse_continue_stmt()

        elif nt == 'nonlocal':
            if self.__level == 0:
                self.__syntax_error('nonlocal declaration outside function')
            a = self.__parse_nonlocal_stmt()

        elif nt == 'global':
            if self.__level == 0:
                self.__syntax_error('global declaration outside function')
            a = self.__parse_global_stmt()

        elif nt == 'break':
            a = self.__parse_break_stmt()

        elif nt == 'return':
            if self.__level == 0:
                self.__syntax_error('return outside function')
            a = self.__parse_return_stmt()

        elif nt == 'fun' or nt == 'func':
            a = self.__parse_func_def_stmt()

        elif nt.ttype == AIL_DOC_STRING:
            a = self.__parse_doc_string_object()

        elif nt == '@':
            a = self.__parse_func_def_with_decorator_stmt()

        elif nt == 'load':
            a = self.__parse_load_stmt()

        elif nt == 'struct':
            a = self.__parse_struct_def_stmt()

        elif nt == 'class':
            a = self.__parse_class_def_stmt()

        elif nt == 'assert':
            a = self.__parse_assert_expr()

        elif nt == 'throw':
            a = self.__parse_throw_expr()

        elif nt == 'try':
            a = self.__parse_try_catch_expr()

        elif nt == 'import':
            a = self.__parse_import_stmt()

        elif class_body and nt in ('private', 'protected'):
            a = self.__parse_modified_assign()

        elif class_body and nt == 'static':
            a = self.__parse_static_assign()

        elif class_body and nt in ('get', 'set'):
            a = self.__parse_property_define()

        elif class_body and nt in tuple(_special_method_map.keys()):
            a = self.__parse_special_method()

        elif nt.value in (_keywords + limit) and nt.ttype != AIL_STRING:
            self.__syntax_error()

        elif nt.ttype not in (AIL_ENTER, AIL_EOF) and \
                (nt.value not in (_keywords + limit) or nt.value == 'not'):
            a = self.__parse_binary_expr(True, True)
            self.__expect_newline()

        elif nt.ttype == AIL_ENTER:
            self.__next_tok()
            return ast.NullLineAST(self.__now_ln)

        elif nt.ttype == AIL_EOF or (
                nt.value in _end_signs and nt.ttype != AIL_STRING):
            return ast.EOFAST(self.__now_ln)

        else:
            self.__syntax_error()

        # ** not use anymore (2021.3.21)
        # if self.__now_tok.ttype != AIL_ENTER:
        #     # a stmt should be end of ENTER
        #     self.__syntax_error()
        #
        # self.__next_tok()  # eat enter

        return a

    def __parse_new_block(self, class_body: bool = False) -> ast.BlockAST:
        if self.__now_tok.ttype != AIL_LLBASKET:
            self.__syntax_error()

        ln = self.__now_ln

        self.__next_tok()

        stmt_list = []

        while self.__now_tok.ttype != AIL_LRBASKET:
            s = self.__parse_stmt(class_body=class_body)

            if s is None:
                self.__syntax_error()

            if not isinstance(s, ast.NullLineAST):
                stmt_list.append(s)

            if isinstance(s, ast.EOFAST):
                self.__syntax_error('block should ends with \'}\'')

        self.__next_tok()  # eat '}'

        return ast.BlockAST(stmt_list, ln, True)

    def __parse_block(self, start='then', end='end',
                      start_msg: str = None, end_msg: str = None,
                      start_enter=True, for_if_else: bool = False,
                      for_program: bool = False,
                      class_body: bool = False) -> ast.BlockAST:
        if self.__now_tok.ttype == AIL_LLBASKET and not for_program:
            return self.__parse_new_block(class_body=class_body)

        ln = self.__now_ln

        if for_if_else:
            if self.__now_tok.ttype != AIL_ENTER:
                self.__syntax_error()

            self.__next_tok()  # eat enter

            if self.__now_tok in ('else', 'elif', 'endif'):
                # not eat, leave to if_else parse
                return ast.BlockAST([], ln)

        elif for_program:
            if self.__now_tok == start:
                self.__next_tok()
        else:
            if self.__now_tok != start:
                self.__syntax_error(start_msg)

            if start_enter and self.__next_tok().ttype != AIL_ENTER:
                self.__syntax_error()

            self.__next_tok()  # eat enter

            if self.__now_tok == end:  # empty block
                self.__next_tok()
                return ast.BlockAST([], ln)

        first = self.__parse_stmt((start, end), class_body=class_body)

        if first is None:
            self.__syntax_error()

        elif isinstance(first, ast.EOFAST):
            self.__syntax_error(end_msg)

        stmtl = []

        if not isinstance(first, ast.NullLineAST):
            stmtl.append(first)

        while self.__now_tok != end:
            if for_if_else and self.__now_tok.value in (
                    'elif', 'else', 'endif'):
                return ast.BlockAST(stmtl, ln)

            s = self.__parse_stmt((start, end), class_body=class_body)

            if s is None:
                self.__syntax_error()

            if isinstance(s, ast.EOFAST):
                if for_program:
                    break
                self.__syntax_error(end_msg)

            if not isinstance(s, ast.NullLineAST):
                stmtl.append(s)

        self.__next_tok()  # eat end

        return ast.BlockAST(stmtl, ln)

    def parse(self, ts: TokenStream,
              source: str, filename: str,
              pyc_mode: bool = False) -> ast.BlockAST:
        self.__init__()
        self.__tok_stream = ts
        self.__filename = filename
        self.__source = source
        self.__tok_list = ts.token_list

        self.__tc = 0
        self.__level = 0  # level 0

        self.__pyc_mode = pyc_mode

        if len(ts.token_list) == 0:
            return ast.BlockAST([], 0)

        while self.__now_tok.ttype == AIL_ENTER:  # skip enter at beginning
            self.__next_tok()

        return self.__parse_block('begin', 'end',
                                  'A program should starts with \'begin\'',
                                  "A program should ends with 'end'",
                                  for_program=True)

    def test(self, ts, source):
        self.__init__()
        self.__tok_stream = ts
        self.__filename = '<test>'
        self.__source = source
        self.__tok_list = ts.token_list
        self.__tc = 0
        self.__level = 0  # level 0

        return self.__parse_print_expr()


_PyTreeT = TypeVar('_PyTreeT')


def _set_lineno(pynode: _PyTreeT, lineno: int) -> _PyTreeT:
    node = pyast.fix_missing_locations(pynode)
    if hasattr(node, 'lineno'):
        node.lineno = lineno
    return node


def _increase_all_lineno(start_lineno: int, node: _PyTreeT) -> _PyTreeT:
    if isinstance(node, list):
        return [_increase_all_lineno(start_lineno, n) for n in node]

    if not isinstance(node, pyast.AST):
        return node

    fields = node._fields
    lineno = _get_lineno(node)

    if lineno is not None:
        _set_lineno(node, lineno + start_lineno)

    for field in fields:
        f = _increase_all_lineno(start_lineno, getattr(node, field))
        setattr(node, field, f)

    return node


def _get_lineno(pynode: pyast.AST) -> int:
    return getattr(pynode, 'lineno', None)


class PyTreeConvertException(Exception):
    def __init__(self, msg: str, ln: int):
        super().__init__(msg)
        self.ln = ln


class ASTConverter:
    def __init__(self):
        self.__block_stmt_append_func_stack = []

    def __append_stmt_to_top_block(self, stmt: pyast.stmt):
        if self.__block_stmt_append_func_stack:
            self.__block_stmt_append_func_stack[-1](stmt)

    def _new_name(self,
                  id: str, ln: int, ctx: pyast.expr_context = load_ctx()) -> pyast.Name:
        return _set_lineno(name_expr(id, ctx), ln)

    def _new_constant(self, value: Union[int, float, str], ln: int) -> pyast.Constant:
        return _set_lineno(constant_expr(value), ln)

    def _new_call_name(self, id: str, args: List[pyast.expr], ln: int) -> pyast.Call:
        return _set_lineno(call_expr(
            self._new_name(id, ln), args
        ), ln)

    def _convert_cell(self, cell: ast.CellAST) -> Union[pyast.Name, pyast.Constant]:
        if cell.value == 'null':
            return _set_lineno(constant_expr(None), cell.ln)
        elif cell.value == 'true':
            return _set_lineno(constant_expr(True), cell.ln)
        elif cell.value == 'false':
            return _set_lineno(constant_expr(True), cell.ln)
        elif cell.type == AIL_NUMBER:
            return _set_lineno(constant_expr(eval(cell.value)), cell.ln)
        elif cell.type == AIL_STRING:
            return _set_lineno(constant_expr(cell.value), cell.ln)
        elif cell.type == AIL_IDENTIFIER:
            return _set_lineno(name_expr(cell.value, load_ctx()), cell.ln)

    def _convert_bin_op_expr(self, left, rights, ln: int) -> pyast.BinOp:
        r_op = rights[0][0]  # right: [[op, right_expr], ...]

        op = {
            '**': pyast.Pow(),
            '+': pyast.Add(),
            '-': pyast.Sub(),
            '*': pyast.Mult(),
            '/': pyast.Div(),
            'mod': pyast.Mod(),
            '<<': pyast.LShift(),
            '>>': pyast.RShift(),
            '|': pyast.BitOr(),
            '&': pyast.BitAnd(),
            '^': pyast.BitXor(),
            '<': pyast.Lt(),
            '>': pyast.Gt(),
            '<=': pyast.LtE(),
            '>=': pyast.GtE(),
            '==': pyast.Eq(),
            '!=': pyast.NotEq(),
            '!==': pyast.IsNot(),
            '===': pyast.Is(),
        }[r_op]

        o_left = left

        left = self.convert(left)

        if len(rights) == 1:
            right = self.convert(rights[0][1])
            if isinstance(op, pyast.cmpop):
                return _set_lineno(compare_expr(left, [op], [right]), ln)
            return _set_lineno(bin_op_expr(left, op, right), ln)

        new_left = ast.GenericBinaryExprAST(o_left, rights[:1], ln)
        right = self._convert_bin_op_expr(new_left, rights[1:], ln)
        return right

    def _convert_call_expr(self, expr: ast.CallExprAST) -> pyast.Call:
        func = self.convert(expr.left)

        o_args: ast.ArgListAST = expr.arg_list

        args = []
        keywords = []

        for arg in o_args.arg_list:
            value = self.convert(arg.expr)
            if arg.star:
                value = _set_lineno(starred_expr(value, load_ctx()), arg.ln)
            elif arg.default:
                value = _set_lineno(
                    keyword_expr(arg.expr.value, self.convert(arg.default)), arg.ln)
                keywords.append(value)
                continue

            args.append(value)

        return _set_lineno(call_expr(func, args, keywords), expr.ln)

    def _convert_print_stmt(self, stmt: ast.PrintStmtAST) -> pyast.Call:
        func = _set_lineno(name_expr('print', load_ctx()), stmt.ln)
        args = [self.convert(expr) for expr in stmt.value_list]

        return _set_lineno(call_expr(func, args), stmt.ln)

    def _convert_input_stmt(
            self, stmt: ast.InputStmtAST) -> Union[pyast.Assign, pyast.Call]:
        # input EXPR, [NAME [',' NAME]*]  ->  __ail_input__(EXPR, name_count)
        expr = self.convert(stmt.msg)
        vals = stmt.value_list.value_list

        input_call = self._new_call_name(
            '__ail_input__', [expr, self._new_constant(len(vals), stmt.ln)],
            stmt.ln,
        )

        if len(vals) > 1:
            targets = _set_lineno(
                tuple_expr([self._new_name(name, stmt.ln, store_ctx()) for name in vals],
                           store_ctx()),
                stmt.ln
            )
            return assign_stmt([targets], input_call)
        elif len(vals) == 1:
            return _set_lineno(assign_stmt(
                [_set_lineno(name_expr(vals[0], store_ctx()), stmt.ln)], input_call
            ), stmt.ln)

        return input_call

    def _convert_bool_expr(self,
                           expr: Union[ast.AndTestAST, ast.OrTestAST],
                           op: pyast.boolop) -> pyast.BoolOp:
        o_values = expr.right.copy()
        o_values.insert(0, expr.left)

        values = []

        for value in o_values:
            e = self.convert(value)
            values.append(e)

        return _set_lineno(bool_op_expr(op, values), expr.ln)

    def _convert_subscript_expr(self, expr: ast.SubscriptExprAST) -> pyast.Subscript:
        left = self.convert(expr.left)
        value = self.convert(expr.expr)

        return _set_lineno(subscript_expr(
            left, _set_lineno(index_slice(value), expr.ln), load_ctx()
        ), expr.ln)

    def _convert_member_access_expr(self, left, rights, ln: int) -> pyast.Attribute:
        o_left = left

        left = self.convert(left)

        if len(rights) == 1:
            right = self.convert(rights[0])
            if isinstance(right, pyast.Call):
                right.func = _set_lineno(
                    attribute_expr(left, right.func.id, load_ctx()), ln)
                return right
            elif isinstance(right, pyast.Subscript):
                right.value = _set_lineno(
                    attribute_expr(left, right.value.id, load_ctx()), ln)
                return right
            return _set_lineno(attribute_expr(left, right.id, load_ctx()), ln)

        new_left = ast.MemberAccessAST(o_left, rights[:1], ln)
        right = self._convert_member_access_expr(new_left, rights[1:], ln)
        return right

    def _convert_unary_expr(self, expr: ast.UnaryExprAST) -> pyast.UnaryOp:
        op = {
            '+': uadd_uop,
            '-': usub_uop,
            '~': invert_uop,
            '!': not_uop,
        }[expr.op]()

        operand = self.convert(expr.right_expr)

        return _set_lineno(unary_op_expr(op, operand), expr.ln)

    def _convert_assign_expr(self,
                             expr: ast.AssignExprAST, as_stmt: bool) -> pyast.Assign:
        if not as_stmt:
            raise PyTreeConvertException('not support assign expression', expr.ln)

        aug_assign = False
        aug_op_map = {
            '**': pyast.Pow(),
            '+': pyast.Add(),
            '-': pyast.Sub(),
            '*': pyast.Mult(),
            '/': pyast.Div(),
            'mod': pyast.Mod(),
            '<<': pyast.LShift(),
            '>>': pyast.RShift(),
            '|': pyast.BitOr(),
            '&': pyast.BitAnd(),
            '^': pyast.BitXor(),
        }
        aug_op = None

        right = self.convert(expr.right)

        if expr.aug_assign:
            aug_assign = True
            rop, right = expr.right.right[0]
            right = self.convert(right)
            aug_op = aug_op_map[rop]

        left = self.convert(expr.left)

        if type(left) not in (pyast.Attribute, pyast.Name, pyast.Subscript, pyast.Tuple):
            raise PyTreeConvertException('illegal assign target', expr.ln)

        left.ctx = store_ctx()

        if isinstance(left, pyast.Tuple):
            for elt in left.elts:
                elt.ctx = store_ctx()

        if aug_assign:
            return _set_lineno(aug_assign_stmt(left, aug_op, right), expr.ln)

        return _set_lineno(assign_stmt([left], right), expr.ln)

    def _convert_while_stmt(self, stmt: ast.WhileStmtAST) -> pyast.While:
        test = self.convert(stmt.test)
        block = self._convert_block(stmt.block, True)

        return _set_lineno(while_stmt(test, block), stmt.ln)

    def _convert_do_loop_stmt(self, stmt: ast.DoLoopStmtAST) -> pyast.While:
        body = self._convert_block(stmt.block, True)
        test = self.convert(stmt.test)
        true_test = _set_lineno(constant_expr(True), stmt.ln)

        break_if = _set_lineno(
            if_stmt(test, [_set_lineno(break_stmt(), stmt.ln)], []),
            stmt.test.ln
        )

        try_body = _set_lineno(
            try_stmt(body, [], [break_if]),
            stmt.test.ln
        )

        return _set_lineno(while_stmt(true_test, [try_body]), stmt.ln)

    def _convert_for_stmt(self, stmt: ast.ForStmtAST) -> List[pyast.stmt]:
        for_stmt = []

        init_block = ast.BlockAST(stmt.init_list.expr_list, stmt.init_list.ln)
        for_stmt.extend(self._convert_block(init_block, True))

        if stmt.test is None:
            test = self._new_constant(True, stmt.ln)
        else:
            test = self.convert(stmt.test)

        update_block = ast.BlockAST(stmt.update_list.expr_list, stmt.update_list.ln)

        body = self._convert_block(stmt.block)
        body_with_try = [_set_lineno(
            try_stmt(body, [], self._convert_block(update_block, True)), stmt.block.ln)]

        while_stmt_ = _set_lineno(while_stmt(test, body_with_try), stmt.ln)
        for_stmt.append(while_stmt_)

        return for_stmt

    def _convert_if_stmt(self, stmt: ast.IfStmtAST) -> pyast.If:
        test = self.convert(stmt.test)
        body = self._convert_block(stmt.block, True)
        else_body = self._convert_block(stmt.else_block, True)

        return _set_lineno(if_stmt(test, body, else_body), stmt.ln)

    def _convert_try_stmt(self, stmt: ast.TryCatchStmtAST) -> pyast.Try:
        try_body = self._convert_block(stmt.try_block, True)
        finally_body = self._convert_block(stmt.finally_block, True)

        handler = _set_lineno(
            _set_lineno(
                except_handler(
                    self._new_name('Exception', stmt.catch_block.ln), stmt.name,
                    self._convert_block(stmt.catch_block, True)),
                stmt.catch_block.ln),
            stmt.catch_block.ln)

        return _set_lineno(try_stmt(try_body, [handler], finally_body), stmt.ln)

    def _convert_function_def_stmt(
            self, func: ast.FunctionDefineAST) -> pyast.FunctionDef:
        name = func.name
        args = self._convert_arguments(func.arg_list)
        body = self._convert_block(func.block, True)
        decorators = [self.convert(expr) for expr in func.decorator]

        if func.bindto:
            decorators.insert(0,
                              self._new_call_name(
                                  '__ail_bind_function__',
                                  [
                                      self._new_constant(name, func.ln),
                                      self._new_name(func.bindto, func.ln)
                                  ],
                                  func.ln)
                              )

        return _set_lineno(function_def_stmt(
            name, args, body, decorators), func.ln)

    def _convert_array_expr(self, array: ast.ArrayAST) -> pyast.List:
        items = [self.convert(item) for item in array.items.item_list]

        return _set_lineno(list_expr(items, load_ctx()), array.ln)

    def _convert_map_expr(self, m: ast.MapAST) -> pyast.Dict:
        keys = [self.convert(k) for k in m.keys]
        values = [self.convert(v) for v in m.values]

        return _set_lineno(dict_expr(keys, values), m.ln)

    def _convert_lambda_expr(self, func: ast.FunctionDefineAST) -> pyast.Lambda:
        expr = self.convert(func.lambda_return)
        args = self._convert_arguments(func.arg_list)
        return _set_lineno(lambda_expr(args, expr), func.ln)

    def _convert_function_def(
            self,
            func: ast.FunctionDefineAST,
            as_stmt: bool) -> Union[pyast.FunctionDef, pyast.Name, pyast.Lambda]:
        if not as_stmt:
            if func.is_lambda:
                return self._convert_lambda_expr(func)
            func.name = '<anonymous %s>' % hash(func)

        func_stmt = self._convert_function_def_stmt(func)

        if as_stmt:
            return func_stmt

        self.__append_stmt_to_top_block(func_stmt)
        return self._new_name(func.name, func.arg_list.ln)

    def _convert_struct_def(self, struct: ast.StructDefineAST) -> pyast.Assign:
        return assign_stmt([self._new_name(struct.name, struct.ln, store_ctx())],
                           self._new_call_name(
                               '__ail_make_struct__',
                               [
                                   self._new_constant(struct.name, struct.ln),
                                   _set_lineno(list_expr(
                                       [self._new_constant(n, struct.ln) for n in struct.name_list],
                                       load_ctx(),
                                   ), struct.ln),
                                   _set_lineno(list_expr(
                                       [self._new_constant(n, struct.ln) for n in struct.protected_list],
                                       load_ctx(),
                                   ), struct.ln),
                               ], struct.ln))

    def _convert_arguments(self, args: ast.ArgListAST) -> pyast.arguments:
        argl = []
        var_arg = None
        kw_arg = None
        defaults = []

        for arg in args.arg_list:
            assert isinstance(arg.expr, ast.CellAST) and \
                   arg.expr.type == AIL_IDENTIFIER

            name = arg.expr.value
            a = _set_lineno(argument(name), arg.ln)

            if arg.star:
                var_arg = a
                continue
            if arg.kw_star:
                kw_arg = a
                continue

            if arg.default:
                d = self.convert(arg.default)
                defaults.append(d)

            argl.append(a)

        return _set_lineno(arguments(argl, var_arg, kw_arg, defaults), args.ln)

    def _convert_class_def_stmt(self, cls: ast.ClassDefineAST) -> pyast.ClassDef:
        bases = [self.convert(b) for b in cls.bases]
        name = cls.name
        decorators = [self.convert(d) for d in cls.func.decorator]
        body = self._convert_block(cls.func.block, True)
        keywords = []

        if cls.meta is not None:
            k = keyword_expr('metaclass', self.convert(cls.meta))
            keywords.append(k)

        return _set_lineno(
            class_def_stmt(name, bases, keywords, body, decorators), cls.ln)

    def _convert_load_stmt(self, load: ast.LoadStmtAST) -> pyast.Call:
        ln = load.ln

        path = load.path
        call = self._new_call_name(
            '__ail_import__',
            [
                self._new_constant(0, ln),
                self._new_constant(path, ln),
                self._new_call_name('locals', [], ln)
            ],
            ln
        )

        return call

    def _convert_import_stmt(self, imp: ast.ImportStmtAST) -> pyast.Call:
        ln = imp.ln

        path = imp.path
        members = imp.members
        if imp.members is None:
            members = []

        call = self._new_call_name(
            '__ail_import__',
            [
                self._new_constant(1, ln),
                self._new_constant(path, ln),
                self._new_call_name('locals', [], ln),
                self._new_constant(imp.name, ln),
                _set_lineno(
                    list_expr(
                        [self._new_constant(m, ln) for m in members], load_ctx()), ln),
            ],
            ln
        )

        return call

    def _convert_block(
            self, block: ast.BlockAST,
            for_module: bool = True) -> List[pyast.stmt]:
        stmts = []

        try:
            self.__block_stmt_append_func_stack.append(stmts.append)
            for stmt in block.stmts:
                s = self.convert(stmt, for_module)
                if isinstance(s, pyast.expr):
                    s = _set_lineno(expr_stmt(s), stmt.ln)
                elif isinstance(s, list):
                    stmts.extend(s)
                    continue
                stmts.append(s)
            if len(stmts) == 0:
                stmts.append(pass_stmt())
            return stmts
        finally:
            self.__block_stmt_append_func_stack.pop()

    def _convert_py_code_block(self, code: ast.PyCodeBlock) -> List[pyast.stmt]:
        module_node = pyast.parse(code.code)
        return _increase_all_lineno(code.ln - 1, module_node.body)

    def convert(self, a, as_stmt: bool = False) -> Union[pyast.AST, List[pyast.stmt]]:
        if isinstance(a, ast.CellAST):
            return self._convert_cell(a)

        elif isinstance(a, ast.UnaryExprAST):
            return self._convert_unary_expr(a)

        elif type(a) in ast.BIN_OP_AST:
            return self._convert_bin_op_expr(a.left, a.right, a.ln)

        elif isinstance(a, ast.CallExprAST):
            return self._convert_call_expr(a)

        elif isinstance(a, ast.PrintStmtAST):
            return self._convert_print_stmt(a)

        elif isinstance(a, ast.InputStmtAST):
            return self._convert_input_stmt(a)

        elif isinstance(a, ast.AndTestAST):
            return self._convert_bool_expr(a, pyast.And())

        elif isinstance(a, ast.OrTestAST):
            return self._convert_bool_expr(a, pyast.Or())

        elif isinstance(a, ast.TestExprAST):
            return self.convert(a.test, as_stmt)

        elif isinstance(a, ast.BlockAST):
            return self._convert_block(a, as_stmt)

        elif isinstance(a, ast.IfStmtAST):
            return self._convert_if_stmt(a)

        elif isinstance(a, ast.WhileStmtAST):
            return self._convert_while_stmt(a)

        elif isinstance(a, ast.DoLoopStmtAST):
            return self._convert_do_loop_stmt(a)

        elif isinstance(a, ast.FunctionDefineAST):
            return self._convert_function_def(a, as_stmt)

        elif isinstance(a, ast.ClassDefineAST):
            return self._convert_class_def_stmt(a)

        elif isinstance(a, ast.ReturnStmtAST):
            return _set_lineno(return_stmt(self.convert(a.expr)), a.ln)

        elif isinstance(a, ast.BreakStmtAST):
            return _set_lineno(break_stmt(), a.ln)

        elif isinstance(a, ast.ContinueStmtAST):
            return _set_lineno(continue_stmt(), a.ln)

        elif isinstance(a, ast.GlobalStmtAST):
            return _set_lineno(global_stmt([a.name]), a.ln)

        elif isinstance(a, ast.NonlocalStmtAST):
            return _set_lineno(nonlocal_stmt([a.name]), a.ln)

        elif isinstance(a, ast.ArrayAST):
            return self._convert_array_expr(a)

        elif isinstance(a, ast.TupleAST):
            return tuple_expr([self.convert(e) for e in a.items], load_ctx())

        elif isinstance(a, ast.MapAST):
            return self._convert_map_expr(a)

        elif isinstance(a, ast.ItemListAST):
            raise PyTreeConvertException('ItemListAST cannot be converted', a.ln)

        elif isinstance(a, ast.SubscriptExprAST):
            return self._convert_subscript_expr(a)

        elif isinstance(a, ast.LoadStmtAST):
            return self._convert_load_stmt(a)

        elif isinstance(a, ast.ImportStmtAST):
            return self._convert_import_stmt(a)

        elif isinstance(a, ast.MemberAccessAST):
            return self._convert_member_access_expr(a.left, a.members, a.ln)

        elif isinstance(a, ast.AssignExprAST):
            return self._convert_assign_expr(a, as_stmt)

        elif isinstance(a, ast.StructDefineAST):
            return self._convert_struct_def(a)

        elif isinstance(a, ast.NotTestAST):
            return self._convert_unary_expr(
                ast.UnaryExprAST('!', a.expr, a.ln)
            )

        elif isinstance(a, ast.ForStmtAST):
            return self._convert_for_stmt(a)

        elif isinstance(a, ast.BinaryExprListAST):
            raise PyTreeConvertException('BinaryExprListAST cannot be converted', a.ln)

        elif isinstance(a, ast.AssertStmtAST):
            return _set_lineno(assert_stmt(self.convert(a.test), None), a.ln)

        elif isinstance(a, ast.ThrowStmtAST):
            return _set_lineno(raise_stmt(self.convert(a.expr)), a.ln)

        elif isinstance(a, ast.TryCatchStmtAST):
            return self._convert_try_stmt(a)

        elif isinstance(a, ast.PyCodeBlock):
            return self._convert_py_code_block(a)

        elif isinstance(a, list):
            raise PyTreeConvertException('list cannot be converted', a.ln)

        return a

    def convert_module(self, block: ast.BlockAST) -> pyast.Module:
        body = self.convert(block, True)

        return _set_lineno(module(body), block.ln)

    def convert_single(self, block: ast.BlockAST) -> pyast.Interactive:
        body = self.convert(block, True)

        return _set_lineno(interactive(body), block.ln)

    def test(self, tree):
        t = self.convert(tree)
        m = pyast.fix_missing_locations(module([expr_stmt(t)]))

        return m


TEST_CONVERT_PYAST = True  # and False


def test_parse():
    import pprint

    source = open('./tests/test.ail').read()
    l = Lex()
    ts = l.lex(source)

    p = Parser()
    t = p.parse(ts, source, '<test>', TEST_CONVERT_PYAST)

    if not TEST_CONVERT_PYAST:
        pt = test_utils.make_ast_tree(t)
        pprint.pprint(pt)
    else:
        import ast
        converter = ASTConverter()

        tree = converter.convert_module(t)

        test_utils.print_pyast(tree)
        test_utils.unparse_pyast(tree)


if __name__ == '__main__':
    test_parse()
