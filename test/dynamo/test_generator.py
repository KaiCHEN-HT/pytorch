# Owner(s): ["module: dynamo"]
import itertools
import unittest
from collections import OrderedDict

import torch
import torch._dynamo.test_case
import torch._dynamo.testing
from torch._dynamo.exc import Unsupported
from torch._dynamo.testing import EagerAndRecordGraphs, normalize_gm
from torch.testing._internal.common_utils import (
    instantiate_parametrized_tests,
    parametrize,
)


class GeneratorTestsBase(torch._dynamo.test_case.TestCase):
    def setUp(self):
        super().setUp()
        torch._dynamo.config.enable_yield_on_generator = True

    def tearDown(self):
        super().tearDown()
        torch._dynamo.config.enable_yield_on_generator = False

    def _compile_check(self, fn):
        eager = EagerAndRecordGraphs()
        t = torch.randn(2)
        torch.compile(fn, backend=eager, fullgraph=True)(t)
        self.assertGreater(len(eager.graphs), 0)


class GeneratorTests(GeneratorTestsBase):
    expected_failures = []

    def run(self, result=None):
        # Override the run method to inject the "expectingFailure" marker
        # when the test case runs.
        marker = "__unittest_expecting_failure__"
        for test_name in dir(self):
            test_method = getattr(self, test_name)
            if test_name.startswith("test_") and not getattr(
                test_method, marker, False
            ):
                getattr(self, test_name).__dict__[marker] = (
                    test_name in self.expected_failures
                )
        return super().run(result=result)

    def test_generator_simple(self):
        def whoo():
            yield 1
            yield 2
            yield 3

        @torch.compile(backend="eager", fullgraph=True)
        def fn(t):
            gen = whoo()
            t = t + next(gen)
            t = t + next(gen)
            t = t + next(gen)
            return t

        t = torch.randn(2)
        y = fn(t)
        self.assertEqual(y, t + 6)

    def test_infinite_generator(self):
        def whoo():
            i = 0
            while True:
                yield i
                i += 1

        @torch.compile(backend="eager", fullgraph=True)
        def fn(t):
            gen = whoo()
            t = t + next(gen)
            t = t + next(gen)
            t = t + next(gen)
            return t

        t = torch.randn(2)
        y = fn(t)
        self.assertEqual(y, t + 3)

    def test_infinite_generator_2(self):
        def whoo(t):
            i = 0
            while True:
                yield t + i
                i += 1

        @torch.compile(backend="eager", fullgraph=True)
        def fn(t):
            return list(zip(range(3), whoo(t)))

        t = torch.randn(2)
        y = fn(t)
        self.assertEqual(y, list(zip(range(3), whoo(t))))

    def test_infinite_generator_3(self):
        def whoo(i):
            while True:
                yield i

        @torch.compile(backend="eager", fullgraph=True)
        def fn(t):
            return list(zip(range(3), whoo(1)))

        t = torch.randn(2)
        y = fn(t)
        self.assertEqual(y, list(zip(range(3), whoo(1))))

    def test_iter(self):
        def whoo():
            i = 0
            while True:
                yield i
                i += 1

        @torch.compile(backend="eager", fullgraph=True)
        def fn(t):
            s = 0
            for i in whoo():
                if i > 5:
                    break
                s += i
            return t + s

        t = torch.randn(2)
        y = fn(t)
        self.assertEqual(y, t + sum(range(6)))

    def test_graph_break_in_generator(self):
        def whoo():
            yield 1
            torch._dynamo.graph_break()
            yield 2

        eager = EagerAndRecordGraphs()

        @torch.compile(backend=eager, fullgraph=False)
        def fn(t):
            gen = whoo()
            s = next(gen)
            s += next(gen)
            return t + s

        t = torch.randn(2)
        y = fn(t)
        self.assertEqual(y, t + 3)
        self.assertEqual(len(eager.graphs), 0)

    def test_graph_break_in_generator_2(self):
        def whoo(x):
            yield x.sin()
            torch._dynamo.graph_break()
            yield x.cos()

        def call_whoo(x):
            gen = whoo(x)
            sin = next(gen)
            cos = next(gen)
            return sin, cos

        eager = EagerAndRecordGraphs()

        @torch.compile(backend=eager, fullgraph=False)
        def fn(t):
            sin, cos = call_whoo(t)
            return sin + cos

        t = torch.randn(2)
        y = fn(t)
        self.assertEqual(y, t.sin() + t.cos())
        self.assertEqual(len(eager.graphs), 1)
        self.assertExpectedInline(
            normalize_gm(eager.graphs[0].print_readable(False)),
            """\
class GraphModule(torch.nn.Module):
    def forward(self, L_stack0_0_: "f32[2]", L_stack0_1_: "f32[2]"):
        l_stack0_0_ = L_stack0_0_
        l_stack0_1_ = L_stack0_1_

        add: "f32[2]" = l_stack0_0_ + l_stack0_1_;  l_stack0_0_ = l_stack0_1_ = None
        return (add,)
""",
        )

    def test_generator_as_argument(self):
        # The inline tracer needs to be kept in sync if an already advanced generator
        # is given to a compiled function.
        def whoo():
            yield 1
            yield 2
            yield 3

        eager = EagerAndRecordGraphs()

        @torch.compile(backend=eager, fullgraph=True)
        def fn(t, ctx):
            return t + next(ctx)

        t = torch.randn(2)
        ctx = whoo()
        next(ctx)
        with self.assertRaisesRegex(
            Unsupported, "Generator as graph argument is not supported"
        ):
            fn(t, ctx)

    def test_generator_as_argument_2(self):
        def whoo(x):
            yield x.sin()
            yield x.cos()

        eager = EagerAndRecordGraphs()

        @torch.compile(backend=eager, fullgraph=True)
        def fn(t, ctx):
            return t + next(ctx)

        t = torch.randn(2)
        ctx = whoo(t)
        next(ctx)
        with self.assertRaisesRegex(
            Unsupported, "Generator as graph argument is not supported"
        ):
            fn(t, ctx)

    def test_generator_as_argument_3(self):
        # The inline tracer needs to be kept in sync if an already advanced generator
        # is given to a compiled function.
        def whoo():
            yield 1
            yield 2
            yield 3

        eager = EagerAndRecordGraphs()

        @torch.compile(backend=eager, fullgraph=True)
        def fn(t, ctx):
            return t + next(ctx)

        t = torch.randn(2)
        ctx = whoo()
        with self.assertRaisesRegex(
            Unsupported, "Generator as graph argument is not supported"
        ):
            fn(t, ctx)

    def test_generator_as_argument_4(self):
        def whoo(x):
            yield x.sin()
            yield x.cos()

        eager = EagerAndRecordGraphs()

        @torch.compile(backend=eager, fullgraph=True)
        def fn(t, ctx):
            return t + next(ctx)

        t = torch.randn(2)
        ctx = whoo(t)
        with self.assertRaisesRegex(
            Unsupported, "Generator as graph argument is not supported"
        ):
            fn(t, ctx)

    def test_islice_chain(self):
        eager = EagerAndRecordGraphs()

        @torch.compile(backend=eager, fullgraph=True)
        def fn(t):
            tmp1 = [t + 1, t + 2]
            tmp2 = [t + 3, t + 4]
            return list(itertools.chain(tmp1, tmp2))

        t = torch.tensor([1.0])
        y = fn(t)
        self.assertEqual(y, [t + 1, t + 2, t + 3, t + 4])

    def test_zip_generator(self):
        def whoo(t):
            yield t + 1
            yield t + 2
            yield t + 3

        @torch.compile(backend="eager", fullgraph=True)
        def fn(t):
            return zip(range(3), whoo(t))

        t = torch.randn(3)
        y = fn(t)
        expected = list(zip(range(3), whoo(t)))
        self.assertEqual(expected, list(y))

    def test_zip_generator_2(self):
        def bar(t, i):
            return t + i

        def whoo(t):
            yield bar(t, 1)
            yield bar(t, 2)
            yield bar(t, 3)

        @torch.compile(backend="eager", fullgraph=True)
        def fn(t):
            return zip(range(3), whoo(t))

        t = torch.randn(3)
        y = fn(t)
        expected = list(zip(range(3), whoo(t)))
        self.assertEqual(expected, list(y))

    def test_zip_subgenerator(self):
        def subgen(t):
            yield t + 1
            yield t + 2

        def whoo(t):
            yield from subgen(t)
            yield t + 3

        @torch.compile(backend="eager", fullgraph=True)
        def fn(t):
            return zip(range(3), whoo(t))

        t = torch.randn(3)
        y = fn(t)
        expected = list(zip(range(3), whoo(t)))
        self.assertEqual(expected, list(y))

    def test_list_zip_generator(self):
        def whoo(t):
            yield t + 1
            yield t + 2
            yield t + 3

        @torch.compile(backend="eager", fullgraph=True)
        def fn(t):
            return list(zip(range(3), whoo(t)))

        t = torch.randn(3)
        y = fn(t)
        expected = list(zip(range(3), whoo(t)))
        self.assertEqual(expected, y)

    def test_zip_infinite_generator(self):
        def whoo(t):
            i = 0
            while True:
                yield t + i
                i += 1

        @torch.compile(backend="eager", fullgraph=True)
        def fn(t):
            return list(zip(range(3), whoo(t)))

        t = torch.randn(3)
        y = fn(t)
        expected = list(zip(range(3), whoo(t)))
        self.assertEqual(expected, y)

    @parametrize("container", [list, tuple, dict, OrderedDict])
    def test_dict_tuple_list_generator(self, container):
        def whoo(t):
            yield 1, t + 1
            yield 2, t + 2
            yield 3, t + 3

        def fn(t):
            gen = whoo(t)
            return container(gen)

        t = torch.randn(2)
        expected = fn(t)
        got = torch.compile(backend="eager", fullgraph=True)(fn)(t)
        self.assertEqual(expected, got)

    def test_return_generator(self):
        def whoo(t):
            yield t + 1
            yield t + 2
            yield t + 3

        @torch.compile(backend="eager", fullgraph=True)
        def fn(t):
            gen = whoo(t)
            return gen

        t = torch.tensor([1.0])
        gen = fn(t)
        self.assertEqual(next(gen), torch.tensor([2.0]))

    def test_return_advanced_generator(self):
        def whoo(t):
            yield t + 1
            yield t + 2
            yield t + 3

        @torch.compile(backend="eager", fullgraph=True)
        def fn(t):
            gen = whoo(t)
            next(gen)
            return gen

        t = torch.tensor([1.0])
        gen = fn(t)
        self.assertEqual(next(gen), torch.tensor([3.0]))

    def test_return_exhaust_generator(self):
        def whoo(t):
            yield t + 1

        @torch.compile(backend="eager", fullgraph=True)
        def fn(t):
            gen = whoo(t)
            next(gen)
            return gen

        t = torch.tensor([1.0])
        gen = fn(t)
        with self.assertRaises(StopIteration):
            next(gen)

    def test_subgenerator(self):
        def subgen(t):
            yield t + 1
            yield t + 2

        def main_gen(t):
            yield from subgen(t)
            yield t + 3

        @torch.compile(backend="eager", fullgraph=True)
        def fn(t):
            gen = main_gen(t)
            return list(gen)

        t = torch.randn(2)
        y = fn(t)
        self.assertEqual(y, [t + 1, t + 2, t + 3])

    def test_return_subgenerator(self):
        def subgen(t):
            yield t + 1
            yield t + 2

        def main_gen(t):
            yield from subgen(t)
            yield t + 3

        @torch.compile(backend="eager", fullgraph=True)
        def fn(t):
            gen = main_gen(t)
            next(gen)
            return gen

        t = torch.randn(2)
        y = fn(t)
        self.assertEqual(list(y), [t + 2, t + 3])

    def test_dynamo_disable_generator(self):
        @torch._dynamo.disable
        def main_gen(t):
            yield t + 1
            yield t + 2
            yield t + 3

        @torch.compile(backend="eager", fullgraph=False)
        def fn(t):
            gen = main_gen(t)
            return list(gen)

        t = torch.randn(2)
        y = fn(t)
        self.assertEqual(y, [t + 1, t + 2, t + 3])

    def test_dynamo_disable_sub_generator(self):
        @torch._dynamo.disable
        def subgen(t):
            yield t + 2
            yield t + 3

        def main_gen(t):
            yield t + 1
            yield from subgen(t)

        @torch.compile(backend="eager", fullgraph=False)
        def fn(t):
            gen = main_gen(t)
            return list(gen)

        t = torch.randn(2)
        y = fn(t)
        self.assertEqual(y, [t + 1, t + 2, t + 3])

    def test_graph_break_outside_generator(self):
        def whoo(t):
            yield t + 1
            yield t + 2

        @torch.compile(backend="eager", fullgraph=False)
        def fn(t):
            gen = whoo(t)
            x = next(gen)
            torch._dynamo.graph_break()
            y = next(gen)
            return x + y

        t = torch.randn(2)
        y = fn(t)
        self.assertEqual(y, (t + 1) + (t + 2))

    def test_graph_break_before_calling_generator(self):
        def whoo(t):
            for perm in itertools.product(itertools.permutations((0, 1, 2)), repeat=1):
                yield sum(perm[0])

        def fn(t):
            s = 0
            for b, p in itertools.product(whoo(t), itertools.permutations((4, 5))):
                s += b
            return s

        t = torch.randn(2)
        expected = fn(t)
        got = torch.compile(backend="eager", fullgraph=False)(fn)(t)
        self.assertEqual(expected, got)

    def test_generator_with_side_effects(self):
        i = 0

        def whoo(t):
            nonlocal i
            for j in range(5):
                i += 1
                yield t + j

        @torch.compile(backend="eager", fullgraph=True)
        def fn(t):
            gen = whoo(t)
            return zip(range(3), gen)

        t = torch.randn(2)
        y = fn(t)
        self.assertEqual(i, 0)
        self.assertEqual(list(y), [(0, t), (1, t + 1), (2, t + 2)])
        self.assertEqual(i, 3)

    def test_subgenerator_with_side_effects(self):
        i = 0

        def subgen(t):
            nonlocal i
            i += 1
            yield t
            i += 1
            yield t + 1

        def whoo(t):
            nonlocal i
            yield from subgen(t)
            i += 1
            yield t + 2
            i += 1
            yield t + 3
            i += 1
            yield t + 4

        @torch.compile(backend="eager", fullgraph=True)
        def fn(t):
            gen = whoo(t)
            return zip(range(3), gen)

        t = torch.randn(2)
        y = fn(t)
        self.assertEqual(i, 0)
        self.assertEqual(list(y), [(0, t), (1, t + 1), (2, t + 2)])
        self.assertEqual(i, 3)

    def test_generator_with_side_effects_graph_break(self):
        i = 0

        def whoo(t):
            nonlocal i
            for j in range(5):
                i += 1
                yield t + j

        @torch.compile(backend="eager", fullgraph=False)
        def fn(t):
            gen = whoo(t)
            torch._dynamo.graph_break()
            return list(zip(range(3), gen))

        t = torch.randn(2)
        y = fn(t)
        self.assertEqual(i, 3)
        self.assertEqual(y, [(0, t), (1, t + 1), (2, t + 2)])

    def test_generator_with_side_effects_graph_break_2(self):
        i = 0

        def whoo(t):
            nonlocal i
            for j in range(5):
                i += 1
                yield t + j
                torch._dynamo.graph_break()

        @torch.compile(backend="eager", fullgraph=False)
        def fn(t):
            gen = whoo(t)
            return list(zip(range(3), gen))

        t = torch.randn(2)
        y = fn(t)
        self.assertEqual(i, 3)
        self.assertEqual(y, [(0, t), (1, t + 1), (2, t + 2)])


class TestGeneratorSend(GeneratorTestsBase):
    def test_send(self):
        def double():
            x = yield
            yield x * 2

        @torch.compile(backend="eager", fullgraph=True)
        def fn(t):
            gen = double()
            next(gen)
            return gen.send(t)

        t = torch.randn(2)
        y = fn(t)
        self.assertEqual(y, t * 2)

    def test_send_stop_iteration(self):
        def double():
            x = yield
            yield x * 2

        @torch.compile(backend="eager", fullgraph=True)
        def fn(t):
            gen = double()
            next(gen)
            a = gen.send(t)
            b = gen.send(t)  # should result in StopIteration
            return a + b

        t = torch.randn(2)
        with self.assertRaises(Unsupported):
            fn(t)


class TestGeneratorClose(GeneratorTestsBase):
    def test_close(self):
        def whoo(t):
            yield t.sin()
            yield t.cos()

        @torch.compile(backend="eager", fullgraph=True)
        def fn(t):
            gen = whoo(t)
            i = next(gen)
            gen.close()
            return i

        t = torch.randn(2)
        y = fn(t)
        self.assertEqual(y, t.sin())

    def test_close_subgen(self):
        z = 0

        def subgen(t):
            nonlocal z
            z = 1
            yield t.sin()
            z = 3
            yield t.cos()

        def whoo(t):
            yield from subgen(t)
            yield t.tan()

        @torch.compile(backend="eager", fullgraph=True)
        def fn(t):
            gen = whoo(t)
            i = next(gen)
            gen.close()
            return i

        t = torch.randn(2)
        y = fn(t)
        self.assertEqual(y, t.sin())
        self.assertEqual(z, 1)

    def test_close_with_side_effects(self):
        L = []
        z = 0

        def whoo(t):
            nonlocal z
            try:
                L.append(1)
                yield t.sin()
                L.append(2)
                yield t.cos()
            finally:
                L.append(z)

        @torch.compile(backend="eager", fullgraph=True)
        def fn(t):
            nonlocal z
            gen = whoo(t)
            i = next(gen)
            z = -123
            gen.close()
            L.append(len(L))
            return i

        t = torch.randn(2)
        y = fn(t)
        self.assertEqual(y, t.sin())
        self.assertEqual(L, [1, -123, 2])

    def test_close_capture_GeneratorExit_return(self):
        z = 0

        def whoo(t):
            nonlocal z
            try:
                z += 1
                yield t.sin()
                yield t.cos()
            except GeneratorExit:
                z += 10
                return t.tan()  # noqa: B901
            finally:
                z += 100

        @torch.compile(backend="eager", fullgraph=True)
        def fn(t):
            nonlocal z
            gen = whoo(t)
            i = next(gen)
            y = gen.close()
            return (i, y)

        t = torch.randn(2)
        (i, y) = fn(t)
        self.assertEqual(i, t.sin())
        self.assertEqual(y, t.tan())
        self.assertEqual(z, 111)

    def test_close_capture_GeneratorExit(self):
        z = 0

        def whoo(t):
            nonlocal z
            try:
                yield t.sin()
                yield t.cos()
            except GeneratorExit:
                yield t.tan()
            finally:
                z = 1

        @torch.compile(backend="eager", fullgraph=True)
        def fn(t):
            nonlocal z
            gen = whoo(t)
            i = next(gen)
            gen.close()
            return i

        t = torch.randn(2)
        # This should actually be RuntimeError("generator ignored GeneratorExit")
        # but Dynamo swallow the exception and raises Unsupported instead
        with self.assertRaisesRegex(Unsupported, "Observed exception"):
            fn(t)

    def test_close_capture_and_reraise_GeneratorExit(self):
        L = []
        z = 0

        def whoo(t):
            nonlocal z
            try:
                L.append(1)
                yield t.sin()
                yield t.cos()
            except GeneratorExit:
                L.append(z)
                z = -1
                raise
            finally:
                L.append(z)

        @torch.compile(backend="eager", fullgraph=True)
        def fn(t):
            nonlocal z
            gen = whoo(t)
            i = next(gen)
            z = -123
            gen.close()
            L.append(456)
            return i

        t = torch.randn(2)
        y = fn(t)
        self.assertEqual(y, t.sin())
        self.assertEqual(L, [1, -123, -1, 456])

    def test_close_capture_and_reraise_RuntimeError(self):
        def whoo(t):
            try:
                yield t.sin()
                yield t.cos()
            except GeneratorExit as e:
                raise RuntimeError from e
            finally:
                pass

        @torch.compile(backend="eager", fullgraph=True)
        def fn(t):
            gen = whoo(t)
            i = next(gen)
            gen.close()
            return i

        t = torch.randn(2)
        with self.assertRaises(RuntimeError):
            fn(t)

    def test_close_with_subgen(self):
        L = []
        z = 0

        def subgen(t):
            yield t.sin()
            yield t.cos()

        def whoo(t):
            nonlocal z
            L.append(10)
            yield from subgen(t)
            L.append(20)
            try:
                L.append(1)
                z = 4
                yield t.tan()
            finally:
                L.append(z)

        @torch.compile(backend="eager", fullgraph=True)
        def fn(t):
            nonlocal z
            gen = whoo(t)
            i = next(gen)
            z = -123
            gen.close()
            L.append(456)
            return i

        t = torch.randn(2)
        y = fn(t)
        self.assertEqual(y, t.sin())
        self.assertEqual(L, [10, 456])
        self.assertEqual(z, -123)

    def test_close_after_close(self):
        z = 0

        def whoo(t):
            nonlocal z
            try:
                z += 1
                yield t.sin()
                yield t.cos()
            finally:
                # finally should only be executed once
                z += 1

        @torch.compile(backend="eager", fullgraph=True)
        def fn(t):
            gen = whoo(t)
            i = next(gen)
            gen.close()
            return (i, gen.close())

        t = torch.randn(2)
        (i, y) = fn(t)
        self.assertEqual(i, t.sin())
        self.assertEqual(y, None)
        self.assertEqual(z, 2)


class TestGeneratorThrow(GeneratorTestsBase):
    def test_throw(self):
        def whoo(t):
            try:
                yield t.sin()
            except ValueError:
                yield t.cos()

        @torch.compile(backend="eager", fullgraph=True)
        def fn(t):
            gen = whoo(t)
            a = next(gen)
            b = gen.throw(ValueError)
            return a + b

        t = torch.randn(2)
        y = fn(t)
        self.assertEqual(y, t.sin() + t.cos())

    def test_throw_no_yield_after_throw(self):
        z = 0

        def whoo(t):
            nonlocal z
            try:
                z += 1
                yield t.sin()
            except ValueError:
                z += 10
            finally:
                z += 100

        @torch.compile(backend="eager", fullgraph=True)
        def fn(t):
            gen = whoo(t)
            a = next(gen)
            try:
                gen.throw(ValueError)
            except StopIteration:
                return a

        t = torch.randn(2)
        y = fn(t)
        self.assertEqual(z, 111)
        self.assertEqual(y, t.sin())

    def test_throw_not_catch(self):
        z = 0

        def whoo(t):
            nonlocal z
            try:
                z += 1
                yield t.sin()
            except ValueError:
                z += 10
                yield t.cos()
            finally:
                z += 100

        @torch.compile(backend="eager", fullgraph=True)
        def fn(t):
            gen = whoo(t)
            a = next(gen)
            b = gen.throw(RuntimeError)
            return a + b

        t = torch.randn(2)
        with self.assertRaises(RuntimeError):
            fn(t)

    def test_throw_raise_difference_exc(self):
        z = 0

        def whoo(t):
            nonlocal z
            try:
                z += 1
                yield t.sin()
            except ValueError as e:
                z += 10
                raise RuntimeError from e
            finally:
                z += 100

        @torch.compile(backend="eager", fullgraph=True)
        def fn(t):
            gen = whoo(t)
            a = next(gen)
            b = gen.throw(ValueError)
            return a + b

        t = torch.randn(2)
        with self.assertRaises(RuntimeError):
            fn(t)

    def test_throw_yield_finally(self):
        z = 0

        def whoo(t):
            nonlocal z
            try:
                z += 1
                yield t.sin()
            except ValueError:
                z += 10
                yield t.cos()
            finally:
                z += 100
                yield t.tan()

        @torch.compile(backend="eager", fullgraph=True)
        def fn(t):
            gen = whoo(t)
            a = next(gen)
            b = gen.throw(ValueError)
            return a + b

        t = torch.randn(2)
        with self.assertRaises(Unsupported):
            fn(t)

    def test_throw_try_except_finally(self):
        z = 0

        def whoo(t):
            nonlocal z
            try:
                z += 1
                yield t.sin()
            except ValueError:
                z += 10
                yield t.cos()
            except RuntimeError:
                z += 100
                yield t.tan()
            finally:
                z += 1000

        @torch.compile(backend="eager", fullgraph=True)
        def fn(t):
            gen = whoo(t)
            a = next(gen)
            b = gen.throw(RuntimeError)
            return a + b

        t = torch.randn(2)
        y = fn(t)
        self.assertEqual(y, t.sin() + t.tan())
        self.assertEqual(z, 1101)

    def test_exception_context_with_yield(self):
        def f():
            yield

        def fn(t):
            gen = f()
            gen.send(None)
            try:
                gen.throw(ValueError)
            except ValueError:
                z = 1
            except Exception as e:
                raise AssertionError from e
            assert z == 1
            return t.sin()

        self._compile_check(fn)


class GeneratorCloseCPythonTests(GeneratorTestsBase):
    # Taken from commit
    # https://github.com/python/cpython/blob/d51a4ca1123e3e49e5cae4273355bdfd9e419a10
    # changed the tests a little bit to run them inside dynamo
    # + replaced all self.assert* calls to plain assert statements

    def test_close_no_return_value(self):
        def f():
            yield

        @torch.compile(backend="eager", fullgraph=True)
        def fn(t):
            gen = f()
            gen.send(None)
            assert gen.close() is None
            return t.sin()

        t = torch.randn(2)
        fn(t)

    def test_close_return_value(self):
        def f():
            try:
                yield
                # close() raises GeneratorExit here, which is caught
            except GeneratorExit:
                return 0  # noqa: B901

        @torch.compile(backend="eager", fullgraph=True)
        def fn(t):
            gen = f()
            gen.send(None)
            assert gen.close() == 0
            return t.sin()

        t = torch.randn(2)
        fn(t)

    def test_close_not_catching_exit(self):
        def f():
            yield
            # close() raises GeneratorExit here, which isn't caught and
            # therefore propagates -- no return value
            return 0  # noqa: B901

        @torch.compile(backend="eager", fullgraph=True)
        def fn(t):
            gen = f()
            gen.send(None)
            assert gen.close() is None
            return t.sin()

        t = torch.randn(2)
        fn(t)

    def test_close_not_started(self):
        def f():
            try:
                yield
            except GeneratorExit:
                return 0  # noqa: B901

        @torch.compile(backend="eager", fullgraph=True)
        def fn(t):
            gen = f()
            assert gen.close() is None
            return t.sin()

        t = torch.randn(2)
        fn(t)

    def test_close_exhausted(self):
        def f():
            try:
                yield
            except GeneratorExit:
                return 0  # noqa: B901

        @torch.compile(backend="eager", fullgraph=True)
        def fn(t):
            gen = f()
            next(gen)
            z = 0
            try:
                next(gen)  # -> StopIteration
            except StopIteration:
                z = 1
            except Exception as e:
                # anything other than StopIteration should fail
                raise AssertionError from e
            assert z == 1
            assert gen.close() is None
            return t.sin()

        t = torch.randn(2)
        fn(t)

    def test_close_closed(self):
        def f():
            try:
                yield
            except GeneratorExit:
                return 0  # noqa: B901

        @torch.compile(backend="eager", fullgraph=True)
        def fn(t):
            gen = f()
            gen.send(None)
            assert gen.close() == 0
            assert gen.close() is None
            return t.sin()

        t = torch.randn(2)
        fn(t)

    def test_close_raises(self):
        def f():
            try:
                yield
            except GeneratorExit:
                pass
            raise RuntimeError

        @torch.compile(backend="eager", fullgraph=True)
        def fn(t):
            gen = f()
            gen.send(None)
            z = 0
            try:
                gen.close()  # -> RuntimeError
            except RuntimeError:
                z = 1
            except Exception as e:
                raise AssertionError from e
            assert z == 1
            return t.sin()

        t = torch.randn(2)
        fn(t)


class GeneratorThrowCpythonTests(GeneratorTestsBase):
    # Taken from commit
    # https://github.com/python/cpython/blob/d51a4ca1123e3e49e5cae4273355bdfd9e419a10
    # changed the tests a little bit to run them inside dynamo
    # + replaced all self.assert* calls to plain assert statements

    @unittest.expectedFailure
    def test_exception_context_with_yield(self):
        def f():
            try:
                raise KeyError("a")
            except Exception:
                yield

        def fn(t):
            gen = f()
            gen.send(None)
            try:
                gen.throw(ValueError)
            except ValueError as e:
                context = e.__context__
                assert (type(context), context.args) == (KeyError, ("a",))
            except Exception as e:
                raise AssertionError from e
            return t.sin()

        self._compile_check(fn)

    @unittest.expectedFailure
    def test_exception_context_with_yield_inside_generator(self):
        # Check that the context is also available from inside the generator
        # with yield, as opposed to outside.
        def f():
            z = 0
            try:
                raise KeyError("a")
            except Exception:
                try:
                    yield
                except Exception as exc:
                    z = 1
                    assert type(exc) == ValueError
                    context = exc.__context__
                    assert (type(context), context.args) == (KeyError, ("a",))
                    yield "b"
                finally:
                    assert z == 1

        def fn(t):
            gen = f()
            gen.send(None)
            actual = gen.throw(ValueError)
            # This ensures that the assertions inside were executed.
            assert actual == "b"
            return t.sin()

        self._compile_check(fn)

    @unittest.expectedFailure
    def test_exception_context_with_yield_from(self):
        def f():
            yield

        def g():
            try:
                raise KeyError("a")
            except Exception:
                yield from f()

        def fn(t):
            gen = g()
            gen.send(None)
            try:
                gen.throw(ValueError)
            except ValueError as e:
                context = e.__context__
                assert (type(context), context.args) == (KeyError, ("a",))
            except Exception as e:
                raise AssertionError from e
            return t.sin()

        self._compile_check(fn)

    def test_exception_context_with_yield_from_with_context_cycle(self):
        # Check trying to create an exception context cycle:
        # https://bugs.python.org/issue40696
        has_cycle = None

        def f():
            yield

        def g(exc):
            nonlocal has_cycle
            try:
                raise exc
            except Exception:
                try:
                    yield from f()
                except Exception as exc:
                    has_cycle = exc is exc.__context__
            yield

        def fn(t):
            exc = KeyError("a")
            gen = g(exc)
            gen.send(None)
            gen.throw(exc)
            # This also distinguishes from the initial has_cycle=None.
            assert has_cycle is False
            return t.sin()

        self._compile_check(fn)

    def test_throw_after_none_exc_type(self):
        def g():
            try:
                raise KeyError
            except KeyError:
                pass

            try:
                yield
            except Exception:
                raise RuntimeError  # noqa: B904

        def fn(t):
            gen = g()
            gen.send(None)
            z = 0
            try:
                gen.throw(ValueError)
            except RuntimeError:
                z += 1
            except Exception:
                raise AssertionError  # noqa: B904
            assert z == 1
            return t.sin()

        self._compile_check(fn)


class GeneratorCpythonTests(GeneratorTestsBase):
    # Taken from commit
    # https://github.com/python/cpython/blob/d51a4ca1123e3e49e5cae4273355bdfd9e419a10
    # changed the tests a little bit to run them inside dynamo
    # + replaced all self.assert* calls to plain assert statements

    def test_send_non_none_to_new_gen(self):
        def f():
            yield 1

        def fn(t):
            g = f()
            z = 0
            try:
                g.send(0)
            except TypeError:
                z += 1
            except Exception as e:
                raise AssertionError from e
            assert z == 1
            assert next(g) == 1
            return t.sin()

        self._compile_check(fn)

    def test_issue103488(self):
        def gen_raises():
            yield 1
            raise ValueError

        def loop():
            try:
                for _ in gen_raises():
                    if True is False:  # noqa: PLR0133
                        return
            except ValueError:
                pass

        def fn(t):
            # This should not raise
            loop()
            return t.sin()

        self._compile_check(fn)


instantiate_parametrized_tests(GeneratorTests)


if __name__ == "__main__":
    from torch._dynamo.test_case import run_tests

    run_tests()
