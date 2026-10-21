from dataclasses import replace

from itchy.itch_ast import AssetExpr, BinaryOpExpr, BoolExpr, Expr, NumberExpr, StringExpr, UnaryOpExpr, VarExpr


class SharedMethods:
    @staticmethod
    def _is_primitive(expr: Expr) -> bool:
        return expr.__class__ in {NumberExpr | StringExpr | AssetExpr | BoolExpr | VarExpr}


    def fold_expr(self, expr: Expr) -> Expr:
        if self._is_primitive(expr):
            return expr

        match expr:
            case UnaryOpExpr(value=value, op=op):
                value = self.fold_expr(value)

                if op == "-" and isinstance(value, NumberExpr):
                    return NumberExpr(
                        -value.value,
                        span=expr.span,
                    )

                if op == "not" and isinstance(value, BoolExpr):
                    return BoolExpr(
                        not value.value,
                        span=expr.span,
                    )

                return replace(expr, value=value)

            case BinaryOpExpr(left=left, right=right, op=op):
                left = self.fold_expr(left)
                right = self.fold_expr(right)

                # reduce operators
                match op:
                    case "!=":
                        return self.fold_expr(
                            UnaryOpExpr("not", BinaryOpExpr(left, "==", right))
                        )
                    case ">=":
                        return self.fold_expr(
                            BinaryOpExpr(
                                BinaryOpExpr(left, ">", right),
                                "or",
                                BinaryOpExpr(left, "==", right)
                            )
                        )
                    case "<=":
                        return self.fold_expr(
                            BinaryOpExpr(
                                BinaryOpExpr(left, "<", right),
                                "or",
                                BinaryOpExpr(left, "==", right)
                            )
                        )
                    case _:
                        pass

                # Numeric constant folding
                if isinstance(left, NumberExpr) and isinstance(right, NumberExpr):
                    match op:
                        case "+":
                            return NumberExpr(
                                left.value + right.value,
                                span=expr.span,
                            )

                        case "-":
                            return NumberExpr(
                                left.value - right.value,
                                span=expr.span,
                            )

                        case "*":
                            return NumberExpr(
                                left.value * right.value,
                                span=expr.span,
                            )

                        case "/":
                            # Let Scratch handle division by zero.
                            if right.value != 0:
                                return NumberExpr(
                                    left.value / right.value,
                                    span=expr.span,
                                )

                        case "==":
                            return BoolExpr(
                                left.value == right.value,
                                span=expr.span,
                            )

                        case ">":
                            return BoolExpr(
                                left.value > right.value,
                                span=expr.span,
                            )

                        case "<":
                            return BoolExpr(
                                left.value < right.value,
                                span=expr.span,
                            )

                        case _:
                            pass

                # Boolean constant folding
                if isinstance(left, BoolExpr) and isinstance(right, BoolExpr):
                    match op:
                        case "and":
                            return BoolExpr(
                                left.value and right.value,
                                span=expr.span,
                            )

                        case "or":
                            return BoolExpr(
                                left.value or right.value,
                                span=expr.span,
                            )

                        case "==":
                            return BoolExpr(
                                left.value == right.value,
                                span=expr.span,
                            )

                        case _:
                            pass

                # Children may have folded even if this expression didn't.
                return replace(
                    expr,
                    left=left,
                    right=right,
                )
            case _:
                return expr