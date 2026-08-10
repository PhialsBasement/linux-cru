"""VESA coordinated video timing calculators.

Implements CVT 1.1 (standard "full" blanking), CVT reduced blanking v1,
and CVT 1.2 reduced blanking v2 (RBv2). Verified against xorg's cvt(1)
output and known RBv2 timings — see tests/test_timings.py.

Which one to use:
- cvt      : CRT-era full blanking; highest pixel clock; what analog
             monitors expect. Rarely the right choice for LCDs.
- cvt-rb   : reduced blanking v1 (fixed 160px hblank). What `cvt -r`
             computes; the VESA spec only defines it for refresh rates
             that are multiples of 60, but the formula works for any.
- cvt-rb2  : reduced blanking v2 (fixed 80px hblank, arbitrary refresh,
             lowest pixel clock). The right default for modern displays
             and refresh overclocking.
"""

from dataclasses import dataclass

# Shared CVT constants (VESA CVT 1.1/1.2)
H_GRANULARITY = 8          # character cell, pixels (CVT + RB1)
MIN_V_PORCH = 3            # lines
MIN_V_BPORCH = 6           # lines
CLOCK_STEP_KHZ = 250       # pixel clock granularity, CVT + RB1
HSYNC_PERCENT = 8          # % of htotal (standard blanking)
MIN_VSYNC_BP_US = 550.0    # microseconds (standard blanking)
C_PRIME = 30.0             # blanking formula: C' = ((C-J)*K/256)+J, C=40 J=20 K=128
M_PRIME = 300.0            # M' = K/256*M, M=600

# Reduced blanking v1
RB_MIN_VBLANK_US = 460.0
RB_H_BLANK = 160
RB_H_SYNC = 32
RB_V_FPORCH = 3

# Reduced blanking v2 (CVT 1.2): fixed 80px hblank, fixed 8-line vsync,
# fixed 6-line back porch (front porch varies), 1px/1kHz granularity.
RB2_H_BLANK = 80
RB2_H_FPORCH = 8
RB2_H_SYNC = 32
RB2_VSYNC = 8
RB2_V_BPORCH = 6

# GTF (the formula that predates CVT). Old CRTs were designed around it,
# and their on-screen geometry is usually calibrated for its blanking.
GTF_MIN_PORCH = 1          # lines
GTF_VSYNC_LINES = 3
GTF_MIN_VSYNC_BP_US = 550.0
GTF_HSYNC_PERCENT = 8.0
GTF_CLOCK_STEP_KHZ = 10    # gtf(1) reports two decimals of MHz

STANDARDS = ("cvt", "cvt-rb", "cvt-rb2", "gtf")


@dataclass(frozen=True)
class Modeline:
    clock_khz: int
    hdisplay: int
    hsync_start: int
    hsync_end: int
    htotal: int
    vdisplay: int
    vsync_start: int
    vsync_end: int
    vtotal: int
    hsync_positive: bool
    vsync_positive: bool

    @property
    def clock_mhz(self) -> float:
        return self.clock_khz / 1000.0

    @property
    def actual_refresh(self) -> float:
        """Refresh rate implied by the rounded pixel clock."""
        return self.clock_khz * 1000.0 / (self.htotal * self.vtotal)

    @property
    def flags(self) -> str:
        return ("+hsync" if self.hsync_positive else "-hsync") + " " + \
               ("+vsync" if self.vsync_positive else "-vsync")

    def timing_string(self) -> str:
        """The xorg modeline body: everything after the mode name."""
        if self.clock_khz % 10 == 0:
            clock = f"{self.clock_mhz:.2f}"
        else:
            clock = f"{self.clock_mhz:.3f}"
        return (f"{clock} {self.hdisplay} {self.hsync_start} {self.hsync_end} "
                f"{self.htotal} {self.vdisplay} {self.vsync_start} {self.vsync_end} "
                f"{self.vtotal} {self.flags}")

    def xorg_modeline(self, name: str) -> str:
        return f'Modeline "{name}" {self.timing_string()}'

    def xrandr_args(self) -> list:
        """Arguments for `xrandr --newmode <name> ...`."""
        return self.timing_string().split()


def _vsync_width(width: int, height: int) -> int:
    """VSync pulse width from aspect ratio, per the CVT spec table."""
    if height % 3 == 0 and width * 3 == height * 4:
        return 4
    if height % 9 == 0 and width * 9 == height * 16:
        return 5
    if height % 10 == 0 and width * 10 == height * 16:
        return 6
    if height % 4 == 0 and width * 4 == height * 5:
        return 7
    if height % 9 == 0 and width * 9 == height * 15:
        return 7
    return 10  # non-standard aspect ratio


def cvt(width: int, height: int, refresh: float) -> Modeline:
    """VESA CVT 1.1 standard (full) blanking. Matches xorg cvt(1)."""
    _check(width, height, refresh)
    hdisplay = width - width % H_GRANULARITY
    vsync = _vsync_width(width, height)

    hperiod = (1_000_000.0 / refresh - MIN_VSYNC_BP_US) / (height + MIN_V_PORCH)
    if hperiod <= 0:
        raise ValueError("refresh rate too high for CVT standard blanking; use cvt-rb2")

    vsync_bp = int(MIN_VSYNC_BP_US / hperiod) + 1
    if vsync_bp < vsync + MIN_V_PORCH:
        vsync_bp = vsync + MIN_V_PORCH
    vtotal = height + MIN_V_PORCH + vsync_bp

    duty = C_PRIME - M_PRIME * hperiod / 1000.0
    if duty < 20.0:
        duty = 20.0
    hblank = int(hdisplay * duty / (100.0 - duty))
    hblank -= hblank % (2 * H_GRANULARITY)
    htotal = hdisplay + hblank

    clock_khz = int(htotal * 1000.0 / hperiod)
    clock_khz -= clock_khz % CLOCK_STEP_KHZ

    hsync = int(htotal * HSYNC_PERCENT / 100)
    hsync -= hsync % H_GRANULARITY
    hsync_end = hdisplay + hblank // 2

    return Modeline(
        clock_khz=clock_khz,
        hdisplay=hdisplay,
        hsync_start=hsync_end - hsync,
        hsync_end=hsync_end,
        htotal=htotal,
        vdisplay=height,
        vsync_start=height + MIN_V_PORCH,
        vsync_end=height + MIN_V_PORCH + vsync,
        vtotal=vtotal,
        hsync_positive=False,
        vsync_positive=True,
    )


def cvt_rb(width: int, height: int, refresh: float) -> Modeline:
    """VESA CVT reduced blanking v1. Matches xorg `cvt -r`."""
    _check(width, height, refresh)
    hdisplay = width - width % H_GRANULARITY
    vsync = _vsync_width(width, height)

    hperiod = (1_000_000.0 / refresh - RB_MIN_VBLANK_US) / height
    if hperiod <= 0:
        raise ValueError("refresh rate too high for this resolution")

    vbi = int(RB_MIN_VBLANK_US / hperiod) + 1
    if vbi < RB_V_FPORCH + vsync + MIN_V_BPORCH:
        vbi = RB_V_FPORCH + vsync + MIN_V_BPORCH
    vtotal = height + vbi
    htotal = hdisplay + RB_H_BLANK

    # libxcvt derives the clock from the estimated horizontal period, not
    # from refresh*vtotal*htotal, so the achieved refresh lands slightly
    # under the requested one (e.g. 59.97 for 60). Match it exactly.
    clock_khz = int(htotal * 1000.0 / hperiod)
    clock_khz -= clock_khz % CLOCK_STEP_KHZ

    hsync_end = hdisplay + RB_H_BLANK // 2

    return Modeline(
        clock_khz=clock_khz,
        hdisplay=hdisplay,
        hsync_start=hsync_end - RB_H_SYNC,
        hsync_end=hsync_end,
        htotal=htotal,
        vdisplay=height,
        vsync_start=height + RB_V_FPORCH,
        vsync_end=height + RB_V_FPORCH + vsync,
        vtotal=vtotal,
        hsync_positive=True,
        vsync_positive=False,
    )


def cvt_rb2(width: int, height: int, refresh: float) -> Modeline:
    """VESA CVT 1.2 reduced blanking v2 (1px/1kHz granularity)."""
    _check(width, height, refresh)
    hperiod = (1_000_000.0 / refresh - RB_MIN_VBLANK_US) / height
    if hperiod <= 0:
        raise ValueError("refresh rate too high for this resolution")

    vbi = int(RB_MIN_VBLANK_US / hperiod) + 1
    min_vbi = 1 + RB2_VSYNC + RB2_V_BPORCH
    if vbi < min_vbi:
        vbi = min_vbi
    vtotal = height + vbi
    vfp = vbi - RB2_VSYNC - RB2_V_BPORCH
    htotal = width + RB2_H_BLANK

    clock_khz = int(refresh * vtotal * htotal / 1000.0)

    hsync_start = width + RB2_H_FPORCH

    return Modeline(
        clock_khz=clock_khz,
        hdisplay=width,
        hsync_start=hsync_start,
        hsync_end=hsync_start + RB2_H_SYNC,
        htotal=htotal,
        vdisplay=height,
        vsync_start=height + vfp,
        vsync_end=height + vfp + RB2_VSYNC,
        vtotal=vtotal,
        hsync_positive=True,
        vsync_positive=False,
    )


def gtf(width: int, height: int, refresh: float) -> Modeline:
    """VESA Generalized Timing Formula. Matches xorg gtf(1).

    Superseded by CVT, but it is what CRT monitors were built around, so
    it is the right choice for one: its generous blanking intervals give
    the electron beam time to retrace, which is what stops the picture
    drifting off the edge of the screen.
    """
    _check(width, height, refresh)
    hdisplay = int(round(width / H_GRANULARITY) * H_GRANULARITY)
    vdisplay = int(round(height))

    h_period_est = (((1.0 / refresh) - GTF_MIN_VSYNC_BP_US / 1e6)
                    / (vdisplay + GTF_MIN_PORCH) * 1e6)
    if h_period_est <= 0:
        raise ValueError("refresh rate too high for this resolution")

    vsync_bp = int(round(GTF_MIN_VSYNC_BP_US / h_period_est))
    vtotal = vdisplay + vsync_bp + GTF_MIN_PORCH

    v_field_rate_est = 1.0 / h_period_est / vtotal * 1e6
    h_period = h_period_est / (refresh / v_field_rate_est)

    duty = C_PRIME - (M_PRIME * h_period / 1000.0)
    hblank = int(round(hdisplay * duty / (100.0 - duty) / (2 * H_GRANULARITY))
                 * (2 * H_GRANULARITY))
    htotal = hdisplay + hblank

    clock_khz = int(round(htotal / h_period * 1000.0))
    clock_khz = int(round(clock_khz / GTF_CLOCK_STEP_KHZ) * GTF_CLOCK_STEP_KHZ)

    hsync = int(round(GTF_HSYNC_PERCENT * htotal / 100.0 / H_GRANULARITY)
                * H_GRANULARITY)
    h_front = hblank // 2 - hsync

    return Modeline(
        clock_khz=clock_khz,
        hdisplay=hdisplay,
        hsync_start=hdisplay + h_front,
        hsync_end=hdisplay + h_front + hsync,
        htotal=htotal,
        vdisplay=vdisplay,
        vsync_start=vdisplay + GTF_MIN_PORCH,
        vsync_end=vdisplay + GTF_MIN_PORCH + GTF_VSYNC_LINES,
        vtotal=vtotal,
        hsync_positive=False,
        vsync_positive=True,
    )


def calc(width: int, height: int, refresh: float, standard: str = "cvt-rb2") -> Modeline:
    if standard == "cvt":
        return cvt(width, height, refresh)
    if standard == "cvt-rb":
        return cvt_rb(width, height, refresh)
    if standard == "cvt-rb2":
        return cvt_rb2(width, height, refresh)
    if standard == "gtf":
        return gtf(width, height, refresh)
    raise ValueError(f"unknown timing standard: {standard!r} (expected one of {STANDARDS})")


def _check(width, height, refresh):
    if width < 16 or height < 16:
        raise ValueError("resolution too small")
    if refresh <= 0:
        raise ValueError("refresh rate must be positive")
