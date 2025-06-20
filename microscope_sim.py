"""
microscope_sim.py
Optogenetic cell-motility simulator (vertex-based stimulation).

Rule change
-----------
- A stimulation mask is a 2-D boolean array (HEIGHTxWIDTH).
- For each cell, every vertex that overlaps a True pixel protrudes
  directly outward (radius += PROTRUSION_GAIN · base_r).

Public API
----------
get_frame(mask: np.ndarray) -> pygame.Surface
"""
from __future__ import annotations
import itertools, math, random, time
from typing import List, Tuple
import numpy as np
import pygame
from PIL import Image, ImageEnhance, ImageFilter

# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #
def wrap(pos: Tuple[float, float], width: int, height: int) -> Tuple[float, float]:
    return (pos[0] % width, pos[1] % height)

def polygon_area(pts) -> float:
    pts = np.asarray(pts)
    x, y = pts[:, 0], pts[:, 1]
    return 0.5 * (x @ np.roll(y, -1) - y @ np.roll(x, -1))

# --------------------------------------------------------------------------- #
# Cell                                                                        #
# --------------------------------------------------------------------------- #
class Cell:
    def __init__(self, width, height, base_radius, vertices, friction, brownian_d, curvature_relax, radial_relax, protrusion_gain, impulse, ruffle_std, rng):
        self.width = width
        self.height = height
        self.base_r = base_radius * (0.85 + 0.3 * rng.random())
        self.r = np.full(vertices, self.base_r)
        self.angles = np.linspace(0, 2 * math.pi, vertices, endpoint=False)
        self.area0 = math.pi * self.base_r ** 2
        self.vel = np.zeros(2)
        self.vertices = vertices
        self.friction = friction
        self.brownian_d = brownian_d
        self.curvature_relax = curvature_relax
        self.radial_relax = radial_relax
        self.protrusion_gain = protrusion_gain
        self.impulse = impulse
        self.ruffle_std = ruffle_std
        self.rng = rng
        self.center = np.array([rng.uniform(0, width), rng.uniform(0, height)], float)

    # ---------------- stimulation ----------------
    def stimulate(self, mask: np.ndarray):
        """
        For every vertex that falls on a True pixel in *mask*,
        increase its radius outward.  No periodic wrapping: the
        mask acts like a real camera sensor.
        """
        # raw vertex coordinates (float)
        vx = self.center[0] + np.cos(self.angles) * self.r
        vy = self.center[1] + np.sin(self.angles) * self.r

        # keep only vertices inside the screen
        inside = (vx >= 0) & (vx < self.width) & (vy >= 0) & (vy < self.height)
        if not inside.any():
            return

        ix = vx[inside].astype(int)
        iy = vy[inside].astype(int)
        hit = mask[iy, ix]          # Boolean array, len = num_inside

        if not hit.any():
            return

        # enlarge only the hit vertices
        idx = np.nonzero(inside)[0][hit]   # indices in self.r that need protrusion
        # 1) protrude stimulated vertices outward
        self.r[idx] += self.protrusion_gain * self.base_r
        self.r = np.clip(self.r, 0.4 * self.base_r, 2.2 * self.base_r)
        self._conserve_area()

         # 2) give the whole cell a kick toward the stimulated side
        #    direction = vector from centre to mean of hit vertices
        vx_hit = vx[idx]
        vy_hit = vy[idx]
        tgt = np.array([vx_hit.mean(), vy_hit.mean()])
        vec = tgt - self.center
        n = np.linalg.norm(vec)
        if n:
            self.vel += (vec / n) * self.impulse

    # ---------------- physics update -------------
    def update(self, dt: float):
        amp = self.rng.normalvariate(0, math.sqrt(2 * self.brownian_d * dt))
        ang = self.rng.uniform(0, 2 * math.pi)
        self.vel += amp * np.array([math.cos(ang), math.sin(ang)])
        self.center += self.vel * dt
        self.center = np.array(wrap(self.center, self.width, self.height))
        self.vel *= max(0.0, 1.0 - self.friction * dt)

        self.r += self.rng.normalvariate(0, self.ruffle_std * self.base_r) * np.cos(
            self.rng.randint(1, 3) * self.angles + self.rng.uniform(0, 2 * math.pi)
        )
        lap = np.roll(self.r, -1) + np.roll(self.r, 1) - 2 * self.r
        self.r += self.curvature_relax * lap + self.radial_relax * (self.base_r - self.r)
        self.r = np.clip(self.r, 0.4 * self.base_r, 2.2 * self.base_r)
        self._conserve_area()

    # ---------------- collision ------------------
    def collide(self, other):
        dvx = other.center[0] - self.center[0]
        dvy = other.center[1] - self.center[1]
        dvx -= self.width * round(dvx / self.width)
        dvy -= self.height * round(dvy / self.height)
        dvec = np.array([dvx, dvy])
        dist = np.linalg.norm(dvec)
        if dist == 0:
            return
        overlap = max(self.r) + max(other.r) - dist
        if overlap <= 0:
            return
        n = dvec / dist
        shift = 0.5 * (overlap + 0.5) * n
        self.center -= shift
        other.center += shift
        self.center = np.array(wrap(self.center, self.width, self.height))
        other.center = np.array(wrap(other.center, other.width, other.height))
        self.vel[:] = 0
        other.vel[:] = 0

    # ---------------- rendering ------------------
    def draw(self, surf: pygame.Surface):
        rel = self._rel_pts()
        layers = 6
        for ox in (-self.width, 0, self.width):
            for oy in (-self.height, 0, self.height):
                pts = [(x + ox + self.center[0], y + oy + self.center[1]) for x, y in rel]
                if not any(0 <= px <= self.width and 0 <= py <= self.height for px, py in pts):
                    continue
                for i in range(layers, 0, -1):
                    s = i / layers
                    shade = 80 + int(100 * s)
                    scaled = [
                        (
                            self.center[0] + ox + (px - (self.center[0] + ox)) * s,
                            self.center[1] + oy + (py - (self.center[1] + oy)) * s,
                        )
                        for px, py in pts
                    ]
                    pygame.draw.polygon(surf, (shade, shade, 255), scaled)
                pygame.draw.polygon(surf, (0, 0, 0), pts, 1)
                pygame.draw.circle(
                    surf,
                    (60, 60, 150),
                    (int(self.center[0] + ox), int(self.center[1] + oy)),
                    int(0.4 * self.base_r),
                )

    # ---------------- helpers --------------------
    def _rel_pts(self):
        return list(zip(np.cos(self.angles) * self.r, np.sin(self.angles) * self.r))

    def _conserve_area(self):
        pts = np.array([(x + self.center[0], y + self.center[1]) for x, y in self._rel_pts()])
        area = abs(polygon_area(pts))
        if area:
            self.r *= math.sqrt(self.area0 / area)

# --------------------------------------------------------------------------- #
# Module‑level state                                                          #
# --------------------------------------------------------------------------- #
pygame.init()

class MicroscopeSim:
    def __init__(
        self,
        width=512,
        height=512,
        nb_cells=30,
        vertices=24,
        base_radius=20,
        friction=3.0,
        brownian_d=80,
        curvature_relax=0.06,
        radial_relax=0.02,
        protrusion_gain=0.05,
        impulse=10,
        ruffle_std=0.06,
        contrast=0.55,
        blur_rad=3,
        noise_std=10,
        brightness=0.78,
        overlay_mask: bool = False,
        rng_seed=0
    ):
        self.width = width
        self.height = height
        self.nb_cells = nb_cells
        self.vertices = vertices
        self.base_radius = base_radius
        self.friction = friction
        self.brownian_d = brownian_d
        self.curvature_relax = curvature_relax
        self.radial_relax = radial_relax
        self.protrusion_gain = protrusion_gain
        self.impulse = impulse
        self.ruffle_std = ruffle_std
        self.contrast = contrast
        self.blur_rad = blur_rad
        self.noise_std = noise_std
        self.brightness = brightness
        self.overlay_mask = overlay_mask
        self.rng = random.Random(rng_seed)
        self._cells: List[Cell] = [
            Cell(
                self.width, self.height, self.base_radius, self.vertices, self.friction, self.brownian_d,
                self.curvature_relax, self.radial_relax, self.protrusion_gain, self.impulse, self.ruffle_std, self.rng
            ) for _ in range(self.nb_cells)
        ]
        self._last_time = time.perf_counter()
        self._cell_layer = pygame.Surface((self.width, self.height))

    def reset(self):
        self._cells = [
            Cell(
                self.width, self.height, self.base_radius, self.vertices, self.friction, self.brownian_d,
                self.curvature_relax, self.radial_relax, self.protrusion_gain, self.impulse, self.ruffle_std, self.rng
            ) for _ in range(self.nb_cells)
        ]
        self._last_time = time.perf_counter()
        self._cell_layer = pygame.Surface((self.width, self.height))

    def get_frame(self, mask: np.ndarray) -> pygame.Surface:
        now = time.perf_counter()
        dt = now - self._last_time
        self._last_time = now

        if mask.dtype != np.bool_:
            mask = mask.astype(bool)

        if mask.any():
            for c in self._cells:
                c.stimulate(mask)

        for c in self._cells:
            c.update(dt)
        for a, b in itertools.combinations(self._cells, 2):
            a.collide(b)

        self._cell_layer.fill((235, 235, 235))
        for c in self._cells:
            c.draw(self._cell_layer)

        frame = self._microscope_filter(self._cell_layer.copy())

        if self.overlay_mask:
            # Overlay the mask as a blue semi-transparent layer, after filtering
            mask_surf = pygame.Surface((self.width, self.height), pygame.SRCALPHA)
            blue_rgb = (50, 140, 255)
            alpha = 128  # semi-transparent
            mask_surf.fill((0, 0, 0, 0))
            arr_rgb = pygame.surfarray.pixels3d(mask_surf)
            arr_alpha = pygame.surfarray.pixels_alpha(mask_surf)
            # Flip mask axes to match pygame's (width, height) order
            mask_T = mask.T
            arr_rgb[mask_T] = blue_rgb
            arr_alpha[mask_T] = alpha
            del arr_rgb, arr_alpha  # unlock the surface
            frame.blit(mask_surf, (0, 0), special_flags=pygame.BLEND_RGBA_ADD)
        return frame

    def _microscope_filter(self, surface: pygame.Surface) -> pygame.Surface:
        raw = pygame.image.tostring(surface, "RGB")
        pil = Image.frombytes("RGB", (self.width, self.height), raw)
        pil = pil.convert("L")
        pil = ImageEnhance.Contrast(pil).enhance(self.contrast)
        pil = pil.filter(ImageFilter.GaussianBlur(self.blur_rad))
        arr = np.asarray(pil, dtype=np.float32)
        arr += np.random.normal(0, self.noise_std, arr.shape)
        arr = np.clip(arr, 0, 255).astype(np.uint8)
        pil = Image.fromarray(arr, mode="L")
        pil = ImageEnhance.Brightness(pil).enhance(self.brightness)
        rgb = pil.convert("RGB")
        return pygame.image.fromstring(rgb.tobytes(), rgb.size, "RGB")
