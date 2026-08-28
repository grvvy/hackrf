#
# This file is part of HackRF.
#
# Copyright (c) 2025 Great Scott Gadgets <info@greatscottgadgets.com>
# SPDX-License-Identifier: BSD-3-Clause

from amaranth               import Module, Signal, Mux, DomainRenamer, signed, unsigned
from amaranth.lib           import wiring, stream, data, memory, fifo
from amaranth.lib.wiring    import In, Out
from amaranth.utils         import ceil_log2

from amaranth_future        import fixed

from dsp.mcm                import ShiftAddMCM


class HalfBandDecimator(wiring.Component):
    def __init__(self, taps, data_shape, shape_out=None, always_ready=False, domain="sync"):
        midtap = taps[len(taps)//2]
        assert taps[1::2] == [0]*(len(taps)//4) + [midtap] + [0]*(len(taps)//4)
        assert midtap == 0.5
        self.taps = taps
        self.data_shape = data_shape
        self.full_shape = FIRFilter.compute_output_shape(data_shape, taps)
        if shape_out is None:
            shape_out = self.full_shape
        self.shape_out = shape_out
        self.always_ready = always_ready
        self._domain = domain
        super().__init__({
            "input":  In(stream.Signature(
                data.ArrayLayout(data_shape, 2),
                always_ready=always_ready
            )),
            "output": Out(stream.Signature(
                data.ArrayLayout(shape_out, 2),
                always_ready=always_ready
            )),
            "enable": In(1),
        })

    @staticmethod
    def interleave_with_zeros(seq, factor):
        out = []
        for n in seq:
            out.append(n)
            out.extend([0]*factor)
        return out[:-factor]

    def elaborate(self, platform):
        m = Module()        

        always_ready = self.always_ready
        taps     = [ 2 * tap for tap in self.taps ]  # scale by 0.5 at the output
        fir_taps = self.interleave_with_zeros(taps[0::2], 1)

        # Arms
        fir_shape_out = (self.output.p[0] << 1).shape()
        m.submodules.fir = fir = FIRFilter(fir_taps, shape=self.data_shape, shape_out=fir_shape_out,
            always_ready=always_ready, num_channels=1, add_tap=len(fir_taps)//2+1)
        fir_out_odd = Signal()
        with m.If(fir.output.valid & fir.output.ready):
            m.d.sync += fir_out_odd.eq(~fir_out_odd)

        odd = Signal()
        with m.If(self.input.valid & self.input.ready):
            m.d.sync += odd.eq(~odd)

        # Only switch modes at even samples.
        switch_stb = Signal()
        m.d.comb += switch_stb.eq((~odd) ^ (self.input.valid & self.input.ready))

        with m.FSM():

            with m.State("BYPASS"):

                with m.If(~self.output.valid | self.output.ready):
                    m.d.sync += self.output.valid.eq(self.input.valid)
                    m.d.sync += self.output.payload.eq(self.input.payload)
                    if not self.input.signature.always_ready:
                        m.d.comb += self.input.ready.eq(1)

                with m.If(self.enable & switch_stb):
                    m.next = "DECIMATE"

            with m.State("DECIMATE"):

                # I and Q channels are muxed in time, demuxed later in the output stage.
                even_buffer = Signal.like(self.input.p, reset_less=True)
                q_value     = Signal.like(self.input.p[1], reset_less=True)
                q_valid     = Signal()

                if not self.input.signature.always_ready:
                    m.d.comb += self.input.ready.eq(fir.input.ready)

                with m.If(self.input.ready & self.input.valid):
                    with m.If(~odd):
                        m.d.sync += even_buffer.eq(self.input.p)
                    with m.Else():
                        m.d.sync += q_value.eq(self.input.p[1])
                        m.d.sync += q_valid.eq(1)

                with m.If(odd):
                    m.d.comb += [
                        fir.add_input   .eq(even_buffer[0]),
                        fir.input.p     .eq(self.input.p[0]),
                        fir.input.valid .eq(self.input.valid),
                    ]
                with m.Else():
                    m.d.comb += [
                        fir.add_input   .eq(even_buffer[1]),
                        fir.input.p     .eq(q_value),
                        fir.input.valid .eq(q_valid),
                    ]
                    with m.If(fir.input.ready):
                        m.d.sync += q_valid.eq(0)

                # Output sum and demux.
                with m.If(~self.output.valid | self.output.ready):
                    if not fir.output.signature.always_ready:
                        m.d.comb += fir.output.ready.eq(1)
                    m.d.sync += self.output.valid.eq(fir.output.valid & fir_out_odd)
                    with m.If(fir.output.valid):
                        m.d.sync += self.output.p[0].eq(self.output.p[1])
                        m.d.sync += self.output.p[1].eq(fir.output.p[0] >> 1)

                # Mode switch logic
                with m.If(~self.enable & switch_stb):
                    m.d.sync += even_buffer.eq(0)
                    m.d.sync += q_value.eq(0)
                    m.next = "BYPASS"

        if self._domain != "sync":
            m = DomainRenamer(self._domain)(m)

        return m


class HalfBandInterpolator(wiring.Component):
    def __init__(self, taps, data_shape, shape_out=None, always_ready=False, domain="sync"):
        midtap = taps[len(taps)//2]
        assert taps[1::2] == [0]*(len(taps)//4) + [midtap] + [0]*(len(taps)//4)
        assert midtap == 0.5
        self.taps = taps
        self.data_shape = data_shape
        self.full_shape = FIRFilter.compute_output_shape(data_shape, [ 2 * tap for tap in taps[0::2]])
        if shape_out is None:
            shape_out = self.full_shape
        self.shape_out = shape_out
        self.always_ready = always_ready
        self._domain = domain
        super().__init__({
            "input":  In(stream.Signature(
                data.ArrayLayout(data_shape, 2),
                always_ready=always_ready
            )),
            "output": Out(stream.Signature(
                data.ArrayLayout(shape_out, 2),
                always_ready=always_ready
            )),
            "enable": In(1),
        })

    def elaborate(self, platform):
        m = Module()        

        always_ready = self.always_ready

        taps      = [ 2 * tap for tap in self.taps ]
        arm0_taps = taps[0::2]
        arm1_taps = taps[1::2]
        delay     = arm1_taps.index(1)

        # Arms
        m.submodules.fir = fir = FIRFilter(arm0_taps, shape=self.data_shape, shape_out=self.shape_out, always_ready=always_ready, num_channels=2)
        m.submodules.dly = dly = Delay(delay, shape=self.data_shape, always_ready=always_ready, num_channels=2)
        m.submodules.dly_fifo = dly_fifo = fifo.SyncFIFOBuffered(width=2*self.data_shape.as_shape().width, depth=fir.delay-1)
        arms = [fir, dly]

        m.d.comb += [
            dly_fifo.w_data.eq(dly.output.p),
            dly_fifo.w_en.eq(dly.output.valid),
        ]
        if not dly.output.signature.always_ready:
            m.d.comb += dly.output.ready.eq(dly_fifo.w_rdy)

        with m.FSM():

            with m.State("BYPASS"):

                with m.If(~self.output.valid | self.output.ready):
                    m.d.sync += self.output.valid.eq(self.input.valid)
                    m.d.sync += self.output.p[0].eq(self.input.p[0])
                    m.d.sync += self.output.p[1].eq(self.input.p[1])
                    if not self.input.signature.always_ready:
                        m.d.comb += self.input.ready.eq(1)

                with m.If(self.enable):
                    m.next = "INTERPOLATE"

            with m.State("INTERPOLATE"):

                # Mode switch logic.
                with m.If(~self.enable):
                    m.next = "BYPASS"

                # Input
                for i, arm in enumerate(arms):
                    m.d.comb += arm.input.payload.eq(self.input.payload)
                    m.d.comb += arm.input.valid.eq(self.input.valid & arms[i^1].input.ready)
                if not self.input.signature.always_ready:
                    m.d.comb += self.input.ready.eq(arms[0].input.ready & arms[1].input.ready)

                # Output

                # Arm index selection: switch after every delivered sample
                arm_index = Signal()

                # Output buffers for each arm.
                r_data_cast = data.ArrayLayout(self.data_shape, 2)(dly_fifo.r_data)

                with m.If(~self.output.valid | self.output.ready):
                    with m.Switch(arm_index):
                        with m.Case(0):
                            m.d.sync += self.output.p[0].eq(fir.output.p[0])
                            m.d.sync += self.output.p[1].eq(fir.output.p[1])
                            m.d.sync += self.output.valid.eq(fir.output.valid)
                            if not fir.output.signature.always_ready:
                                m.d.comb += fir.output.ready.eq(1)
                            with m.If(fir.output.valid):
                                m.d.sync += arm_index.eq(1)
                        with m.Case(1):
                            m.d.sync += self.output.p[0].eq(r_data_cast[0])
                            m.d.sync += self.output.p[1].eq(r_data_cast[1])
                            m.d.sync += self.output.valid.eq(dly_fifo.r_rdy)
                            m.d.comb += dly_fifo.r_en.eq(1)
                            with m.If(dly_fifo.r_rdy):
                                m.d.sync += arm_index.eq(0)

        if self._domain != "sync":
            m = DomainRenamer(self._domain)(m)

        return m


class FIRFilter(wiring.Component):

    def __init__(self, taps, shape, shape_out=None, always_ready=False, num_channels=1, add_tap=None):
        self.taps = list(taps)
        self.add_tap = add_tap
        self.shape = shape
        self.full_shape = self.compute_output_shape(self.shape, self.taps, self.add_tap)
        if shape_out is None:
            shape_out = self.full_shape
        self.saturate = self.full_shape.i_bits > shape_out.i_bits
        self.shape_out = shape_out
        self.num_channels = num_channels
        self.always_ready = always_ready

        sig = {
            "input":  In(stream.Signature(
                data.ArrayLayout(shape, num_channels),
                always_ready=always_ready
            )),
            "output": Out(stream.Signature(
                data.ArrayLayout(shape_out, num_channels),
                always_ready=always_ready
            ))
        }
        if add_tap is not None:
            sig |= {"add_input": In(data.ArrayLayout(shape, num_channels))}
        
        super().__init__(sig)

    @property
    def delay(self):
        d = 2  # base delay value
        if self.full_shape != self.shape_out:
            d += 1
        # saturation is done combinatorially, no additional delay
        return d

    @staticmethod
    def compute_output_shape(shape, taps, add_tap=None):
        # Compute output shape taking into account the actual coefficients.
        _signed = shape.signed | any(t<0 for t in taps)
        taps_as_ratios = [ fixed.Const(tap).as_integer_ratio() for tap in taps if tap != 0 ]
        if add_tap is not None:
            taps_as_ratios +=  [(1,1)]
        max_denom = max(abs(denom) for _, denom in taps_as_ratios)
        f_bits = ceil_log2(max_denom)
        t_bits = max(f_bits, ceil_log2(sum(abs(num) * max_denom // denom for num, denom in taps_as_ratios)))
        base_shape = signed if _signed else unsigned
        return fixed.Shape(base_shape(shape.as_shape().width + t_bits), shape.f_bits + f_bits)

    @staticmethod
    def xform_stage(m, in_stream, xform, domain="sync"):

        # Create output stream.
        out_shape = xform(in_stream.p[0]).shape()
        num_channels = len(in_stream.p)
        out_stream = stream.Signature(
            data.ArrayLayout(out_shape, num_channels),
            always_ready=in_stream.signature.always_ready
        ).create()

        # Assign transformed stream.
        if domain == "comb":
            m.d.comb += out_stream.valid.eq(in_stream.valid)
            for c in range(num_channels):
                m.d.comb += out_stream.p[c].eq(xform(in_stream.p[c]))
            if not in_stream.signature.always_ready:
                m.d.comb += in_stream.ready.eq(out_stream.ready)
        else:
            with m.If(~out_stream.valid | out_stream.ready):
                if not in_stream.signature.always_ready:
                    m.d.comb += in_stream.ready.eq(1)
                m.d.sync += out_stream.valid.eq(in_stream.valid)
                for c in range(num_channels):
                    m.d.sync += out_stream.p[c].eq(xform(in_stream.p[c]))
        
        return out_stream

    def elaborate(self, platform):
        m = Module()

        # Implement transposed-form FIR because it needs a smaller number of registers.

        # Implement constant multipliers.
        nz_taps = [ t for t in self.taps if t != 0]
        m.submodules.mcm = mcm = ShiftAddMCM(
            self.shape.as_shape().width,
            [ fixed.Const(tap).as_integer_ratio()[0] for tap in nz_taps ],
            num_channels=self.num_channels,
            always_ready=self.always_ready)
        wiring.connect(m, wiring.flipped(self.input), mcm.input)

        # Cast outputs to fixed point values.
        muls = dict()
        for i, tap in enumerate(nz_taps):
            mul_shape = (fixed.Const(tap) * self.input.p[0]).shape()
            muls[tap] = [ mul_shape(mcm.output.p[c][f"{i}"]) for c in range(self.num_channels) ]

        # FIR output stream (full precision).
        fir_output = stream.Signature(
            data.ArrayLayout(self.full_shape, self.num_channels),
            always_ready=self.always_ready,
        ).create()
        with m.If(~fir_output.valid | fir_output.ready):
            if not self.always_ready:
                m.d.comb += mcm.output.ready.eq(1)
            m.d.sync += fir_output.valid.eq(mcm.output.valid)

        # Carry sum
        if self.add_tap is not None:
            add_input_q = Signal.like(self.add_input)
            # TODO: prepare to deal with MCM delay different to 1.
            with m.If(self.input.valid & self.input.ready):
                m.d.sync += add_input_q.eq(self.add_input)

        # Implement adder line.
        advance = Signal()
        m.d.comb += advance.eq(mcm.output.valid & mcm.output.ready)

        def _add(acc, val):
            return acc + val if acc is not None else val

        for c in range(self.num_channels):

            accum = None
            for i, tap in enumerate(self.taps[::-1]):
                
                value = accum
                if tap != 0:
                    value = _add(value, muls[tap][c])
                if i == self.add_tap:
                    value = _add(value, add_input_q[c])
                if value is None:
                    continue

                # Create a register with the smallest shape that can hold `value``.
                accum_shape = self.compute_output_shape(
                    self.shape,
                    self.taps[::-1][:i+1],
                    add_tap=(1 if self.add_tap is not None and i>=self.add_tap else 0)
                )
                accum = Signal(accum_shape, name=f"add_{c}_{i}")

                with m.If(advance):
                    m.d.sync += accum.eq(value)

            m.d.comb += fir_output.payload[c].eq(accum)

        # Perform rounding and saturation when needed.
        last_output = fir_output
        if self.full_shape != self.shape_out:  # round
            last_output = self.xform_stage(m, last_output, lambda t: t.round(self.shape_out.f_bits, clip=not self.saturate))
        if self.saturate:
            last_output = self.xform_stage(m, last_output, lambda t: t.saturate(self.shape_out), "comb")

        wiring.connect(m, last_output, wiring.flipped(self.output))

        return m


class Delay(wiring.Component):
    def __init__(self, delay, shape, always_ready=False, num_channels=1):
        self.delay = delay
        self.shape = shape
        self.num_channels = num_channels

        super().__init__({
            "input":  In(stream.Signature(
                data.ArrayLayout(shape, num_channels),
                always_ready=always_ready
            )),
            "output": Out(stream.Signature(
                data.ArrayLayout(shape, num_channels),
                always_ready=always_ready
            )),
        })

    def elaborate(self, platform):
        if self.delay < 3:
            return self.elaborate_regs()
        return self.elaborate_memory()
    
    def elaborate_regs(self):
        m = Module()

        last = self.input.payload
        for i in range(self.delay + 1):
            reg = Signal.like(last, name=f"reg_{i}")
            with m.If(self.input.valid & self.input.ready):
                m.d.sync += reg.eq(last)
            last = reg
        m.d.comb += self.output.payload.eq(last)

        with m.If(self.output.ready | ~self.output.valid):
            if not self.input.signature.always_ready:
                m.d.comb += self.input.ready.eq(1)
            m.d.sync += self.output.valid.eq(self.input.valid)

        return m

    def elaborate_memory(self):
        m = Module()

        m.submodules.mem = mem = memory.Memory(
            shape=self.input.payload.shape(),
            depth=self.delay,
            init=()
        )
        mem_wr = mem.write_port(domain="sync")
        mem_rd = mem.read_port(domain="sync")

        addr = Signal.like(mem_wr.addr)
        with m.If(self.input.valid & self.input.ready):
            m.d.sync += addr.eq(Mux(addr == self.delay-1, 0, addr + 1))

        m.d.comb += [
            mem_wr.addr         .eq(addr),
            mem_rd.addr         .eq(addr),
            mem_wr.data         .eq(self.input.payload),
            mem_wr.en           .eq(self.input.valid & self.input.ready),
            mem_rd.en           .eq(self.input.valid & self.input.ready),
            self.output.payload .eq(mem_rd.data),
        ]

        with m.If(self.output.ready | ~self.output.valid):
            if not self.input.signature.always_ready:
                m.d.comb += self.input.ready.eq(1)
            m.d.sync += self.output.valid.eq(self.input.valid)

        return m


#
# Tests
#

import unittest
import numpy as np
from amaranth.sim import Simulator

class _TestFilter(unittest.TestCase):

    rng = np.random.default_rng(0)

    def _generate_samples(self, count, width, f_bits=0):
        # Generate `count` random samples.
        samples = self.rng.normal(0, 1, count)

        # Convert to integer.
        samples = np.clip(samples, -1.0, 1.0)
        samples = np.round(samples * (2**(width-1) - 1)).astype(int)
        assert max(samples) < 2**(width-1) and min(samples) >= -2**(width-1)  # sanity check

        if f_bits > 0:
            return samples / (1 << f_bits)
        return samples

    def _filter(self, dut, samples, count, num_channels=1, outfile=None, empty_cycles=0, empty_ready_cycles=0):

        async def input_process(ctx):
            if hasattr(dut, "enable"):
                ctx.set(dut.enable, 1)
            await ctx.tick()

            for i, sample in enumerate(samples):
                if num_channels > 1:
                    ctx.set(dut.input.payload, [s.item() for s in sample])
                else:
                    if isinstance(dut.input.payload.shape(), data.ArrayLayout):
                        ctx.set(dut.input.payload, [sample.item()])
                    else:
                        ctx.set(dut.input.payload, sample.item())
                ctx.set(dut.input.valid, 1)
                await ctx.tick().until(dut.input.ready)
                ctx.set(dut.input.valid, 0)
                if empty_cycles > 0:
                    await ctx.tick().repeat(empty_cycles)

        filtered = []
        async def output_process(ctx):
            if not dut.output.signature.always_ready:
                ctx.set(dut.output.ready, 1)
            while len(filtered) < count:
                payload, = await ctx.tick().sample(dut.output.payload).until(dut.output.valid)
                if num_channels > 1:
                    filtered.append([v.as_float() for v in payload])
                else:
                    if isinstance(payload.shape(), data.ArrayLayout):
                        filtered.append(payload[0].as_float())
                    else:
                        filtered.append(payload.as_float())
                if empty_ready_cycles > 0:
                    ctx.set(dut.output.ready, 0)
                    await ctx.tick().repeat(empty_ready_cycles)
                    ctx.set(dut.output.ready, 1)
            if not dut.output.signature.always_ready:
                ctx.set(dut.output.ready, 0)

        sim = Simulator(dut)
        sim.add_clock(1/100e6)
        sim.add_testbench(input_process)
        sim.add_testbench(output_process)
        if outfile:
            with sim.write_vcd(outfile):
                sim.run()
        else:
            sim.run()
        
        return filtered


class TestFIRFilter(_TestFilter):

    def test_filter(self):
        taps = [-1, 0, 9, 16, 9, 0, -1]
        taps = [ tap / 32 for tap in taps ]

        num_samples = 1024
        input_width = 8
        input_samples = self._generate_samples(num_samples, input_width, f_bits=7)

        # Compute the expected result
        filtered_np = np.convolve(input_samples, taps).tolist()

        # Simulate DUT
        dut = FIRFilter(taps, shape=fixed.SQ(1, 7), always_ready=False)
        filtered = self._filter(dut, input_samples, len(input_samples), empty_ready_cycles=5)

        self.assertListEqual(filtered_np[:len(filtered)], filtered)

    def test_filter_with_rounding_and_saturation(self):
        taps = [-1, 0, 9, 16, 9, 0, -1]
        taps = [ tap / 32 for tap in taps ]

        num_samples = 1024
        input_width = 8
        input_samples = self._generate_samples(num_samples, input_width, f_bits=7)

        # Simulate DUT
        dut = FIRFilter(taps, shape=fixed.SQ(1, 7), shape_out=fixed.SQ(1, 7), always_ready=False)
        filtered = self._filter(dut, input_samples, len(input_samples), empty_ready_cycles=5)

        # Compute the expected result
        filtered_np = np.convolve(input_samples, taps).tolist()
        filtered_np = [ round(n * 2**dut.shape_out.f_bits) / 2**dut.shape_out.f_bits for n in filtered_np ]
        filtered_np = np.clip(filtered_np, dut.shape_out.min().as_float(), dut.shape_out.max().as_float()).tolist()

        self.assertListEqual(filtered_np[:len(filtered)], filtered)


class TestHalfBandDecimator(_TestFilter):

    def test_filter(self):

        common_dut_options = dict(
            data_shape=fixed.SQ(1,7),
        )

        taps0 = (np.array([-1, 0, 9, 16, 9, 0, -1]) / 32).tolist()
        taps1 = (np.array([-2, 0, 7, 0, -18, 0, 41, 0, -92, 0, 320, 512, 320, 0, -92, 0, 41, 0, -18, 0, 7, 0, -2]) / 1024).tolist()


        inputs = {

            "test_filter_with_backpressure": {
                "num_samples": 1024,
                "dut_options": dict(**common_dut_options, always_ready=False, taps=taps0),
                "sim_opts": dict(empty_cycles=0),
            },

            "test_filter_with_backpressure_and_rounding": {
                "num_samples": 1024,
                "dut_options": dict(**common_dut_options, shape_out=fixed.SQ(2, 7), always_ready=False, taps=taps0),
                "sim_opts": dict(empty_cycles=0),
            },

            "test_filter_with_backpressure_and_empty_cycles": {
                "num_samples": 1024,
                "dut_options": dict(**common_dut_options, always_ready=False, taps=taps0),
                "sim_opts": dict(empty_cycles=3),
            },

            "test_filter_with_backpressure_taps1": {
                "num_samples": 1024,
                "dut_options": dict(**common_dut_options, always_ready=False, taps=taps1),
                "sim_opts": dict(empty_cycles=0),
            },

            "test_filter_with_backpressure_taps1_rounding_and_saturation": {
                "num_samples": 1024,
                "dut_options": dict(**common_dut_options, shape_out=fixed.SQ(1, 7), always_ready=False, taps=taps1),
                "sim_opts": dict(empty_cycles=0),
            },

            "test_filter_no_backpressure_and_empty_cycles_taps1": {
                "num_samples": 1024,
                "dut_options": dict(**common_dut_options, always_ready=True, taps=taps0),
                "sim_opts": dict(empty_cycles=6),
            },

            "test_filter_no_backpressure": {
                "num_samples": 1024,
                "dut_options": dict(**common_dut_options, always_ready=True, taps=taps1),
                "sim_opts": dict(empty_cycles=3),
            },
        }
        
        for name, scenario in inputs.items():

            with self.subTest(name):
                taps        = scenario["dut_options"]["taps"]
                num_samples = scenario["num_samples"]

                input_width = 8
                samples_i_in = self._generate_samples(num_samples, input_width, f_bits=7)
                samples_q_in = self._generate_samples(num_samples, input_width, f_bits=7)

                # Simulate DUT
                dut = HalfBandDecimator(**scenario["dut_options"])
                filtered = self._filter(dut, zip(samples_i_in, samples_q_in), len(samples_i_in) // 2, num_channels=2, **scenario["sim_opts"])
                filtered_i = [ x[0] for x in filtered ]
                filtered_q = [ x[1] for x in filtered ]

                # Compute the expected result
                filtered_i_np = np.convolve(samples_i_in, taps)[1::2].tolist()
                filtered_q_np = np.convolve(samples_q_in, taps)[1::2].tolist()
                if "shape_out" in scenario["dut_options"]:
                    f_bits = scenario["dut_options"]["shape_out"].f_bits
                    filtered_i_np = [ round(n * 2**f_bits) / 2**f_bits for n in filtered_i_np ]
                    filtered_q_np = [ round(n * 2**f_bits) / 2**f_bits for n in filtered_q_np ]
                    filtered_i_np = np.clip(filtered_i_np, dut.shape_out.min().as_float(), dut.shape_out.max().as_float()).tolist()
                    filtered_q_np = np.clip(filtered_q_np, dut.shape_out.min().as_float(), dut.shape_out.max().as_float()).tolist()

                self.assertListEqual(filtered_i_np[:len(filtered_i)], filtered_i)
                self.assertListEqual(filtered_q_np[:len(filtered_q)], filtered_q)


class TestHalfBandInterpolator(_TestFilter):

    def test_filter(self):

        common_dut_options = dict(
            data_shape=fixed.SQ(1,7),
        )

        taps0 = (np.array([-1, 0, 9, 16, 9, 0, -1]) / 32).tolist()
        taps1 = (np.array([-2, 0, 7, 0, -18, 0, 41, 0, -92, 0, 320, 512, 320, 0, -92, 0, 41, 0, -18, 0, 7, 0, -2]) / 1024).tolist()

        inputs = {

            "test_filter_with_backpressure": {
                "num_samples": 1024,
                "dut_options": dict(**common_dut_options, always_ready=False, taps=taps1),
                "sim_opts": dict(empty_cycles=0, empty_ready_cycles=0),
            },

            "test_filter_with_backpressure_and_rounding": {
                "num_samples": 1024,
                "dut_options": dict(**common_dut_options, shape_out=fixed.SQ(1, 7), always_ready=False, taps=taps1),
                "sim_opts": dict(empty_cycles=0, empty_ready_cycles=0),
            },

            "test_filter_with_backpressure_and_empty_cycles": {
                "num_samples": 1024,
                "dut_options": dict(**common_dut_options, always_ready=False, taps=taps0),
                "sim_opts": dict(empty_ready_cycles=7, empty_cycles=3),
            },

            "test_filter_with_backpressure_taps1": {
                "num_samples": 1024,
                "dut_options": dict(**common_dut_options, always_ready=False, taps=taps1),
                "sim_opts": dict(empty_ready_cycles=7, empty_cycles=0),
            },

            "test_filter_no_backpressure_and_empty_cycles_taps1": {
                "num_samples": 1024,
                "dut_options": dict(**common_dut_options, always_ready=True, taps=taps0),
                "sim_opts": dict(empty_cycles=8),
            },

            "test_filter_no_backpressure": {
                "num_samples": 1024,
                "dut_options": dict(**common_dut_options, always_ready=True, taps=taps1),
                "sim_opts": dict(empty_cycles=16),
            },

        }

    
        for name, scenario in inputs.items():
            with self.subTest(name):
                taps        = scenario["dut_options"]["taps"]
                num_samples = scenario["num_samples"]

                input_width = 8
                samples_i_in = self._generate_samples(num_samples, input_width, f_bits=7)
                samples_q_in = self._generate_samples(num_samples, input_width, f_bits=7)

                # Simulate DUT
                dut = HalfBandInterpolator(**scenario["dut_options"])
                filtered = self._filter(dut, zip(samples_i_in, samples_q_in), len(samples_i_in) * 2, num_channels=2, **scenario["sim_opts"])
                filtered_i = [ x[0] for x in filtered ]
                filtered_q = [ x[1] for x in filtered ]

                # Compute the expected result
                input_samples_pad = np.zeros(2*len(samples_i_in))
                input_samples_pad[0::2] = 2*samples_i_in  # pad with zeros, adjust gain
                filtered_i_np = np.convolve(input_samples_pad, taps).tolist()
                input_samples_pad = np.zeros(2*len(samples_q_in))
                input_samples_pad[0::2] = 2*samples_q_in  # pad with zeros, adjust gain
                filtered_q_np = np.convolve(input_samples_pad, taps).tolist()
                if "shape_out" in scenario["dut_options"]:
                    f_bits = scenario["dut_options"]["shape_out"].f_bits
                    filtered_i_np = [ round(n * 2**f_bits) / 2**f_bits for n in filtered_i_np ]
                    filtered_q_np = [ round(n * 2**f_bits) / 2**f_bits for n in filtered_q_np ]
                    filtered_i_np = np.clip(filtered_i_np, dut.shape_out.min().as_float(), dut.shape_out.max().as_float()).tolist()
                    filtered_q_np = np.clip(filtered_q_np, dut.shape_out.min().as_float(), dut.shape_out.max().as_float()).tolist()

                self.assertListEqual(filtered_i_np[:len(filtered_i)], filtered_i)
                self.assertListEqual(filtered_q_np[:len(filtered_q)], filtered_q)

if __name__ == "__main__":
    unittest.main()
