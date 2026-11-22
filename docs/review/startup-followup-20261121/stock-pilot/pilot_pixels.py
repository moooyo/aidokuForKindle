"""Measure the visible Library and an observed sort-panel response in one R3 visit."""

import importlib.util
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path


def load_observer():
    here = Path(__file__).resolve()
    source = here.with_name('observer.py')
    if not source.is_file():
        source = here.parents[2] / 'startup-native-20261121' / 'observer' / 'observer.py'
    spec = importlib.util.spec_from_file_location('r3_frame_observer', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


observer = load_observer()


@lru_cache(maxsize=2)
def panel_regions(turn):
    points = []
    for raw_y in observer.YS:
        for raw_x in observer.XS:
            column, row = (observer.WIDTH - 1 - raw_x, observer.HEIGHT - 1 - raw_y) if turn else (raw_x, raw_y)
            points.append((column / 2, row / 2))

    def region(left, top, right, bottom):
        result = tuple(index for index, (column, row) in enumerate(points)
                       if left <= column < right and top <= row < bottom)
        observer.require(len(result) >= 4, 'panel_region_too_small')
        return result

    options = tuple(region(left, top + 5, left + 52, top + 49)
                    for top in (187, 251, 315, 379) for left in (594, 820))
    directions = tuple(region(left, 451, left + 52, 492) for left in (594, 820))
    done = region(450, 610, 600, 653) + region(720, 610, 867, 653)
    header = region(564, 131, 876, 177)
    title = region(438, 136, 552, 175)
    return options, directions, done, header, title


def sort_panel(pixels, regions, light):
    options, directions, done, header, title = regions

    def ink_count(mask):
        return sum(pixels[index] < 86 if light else pixels[index] > 170 for index in mask)

    def fill(mask):
        if 10 * ink_count(mask) >= 9 * len(mask):
            return 'ink'
        paper_count = sum(pixels[index] > 170 if light else pixels[index] < 86 for index in mask)
        return 'paper' if 10 * paper_count >= 9 * len(mask) else None

    def one_selected(group):
        fills = [fill(mask) for mask in group]
        return None not in fills and fills.count('ink') == 1

    return (fill(done) == 'ink' and fill(header) == 'paper' and 0 < ink_count(title) < len(title)
            and one_selected(options) and one_selected(directions))


def analyze(rule_path, trace_path, stock_path, brackets):
    require = observer.require
    require(isinstance(brackets, (list, tuple)) and len(brackets) == 4
            and all(type(value) is int for value in brackets), 'missing_release_brackets')
    down_before, down_after, up_before, up_after = brackets
    require(0 < down_before <= down_after < up_before <= up_after
            and down_after - down_before <= observer.ANCHOR_WIDTH
            and up_after - up_before <= observer.ANCHOR_WIDTH, 'invalid_release_brackets')
    details, target, masks, boundary = observer.rule(Path(rule_path))
    stamp, stock_pixels = observer.frame(Path(stock_path))
    rows, diagnostics = observer.trace(Path(trace_path), details)
    require(stamp[:3] == details[:3] and stamp[5] <= rows[0][0]
            and 0 < down_before - stamp[5] <= observer.STOCK_MAX_AGE
            and up_after < rows[-1][0], 'stock_or_release_outside_visit')
    idle = observer.sample(stock_pixels)
    text_masks = masks[:boundary]
    for mask in text_masks:
        observer.require_contrast(bytes(target[index] for index in mask), bytes(idle[index] for index in mask))
    text = tuple(index for mask in text_masks for index in mask)
    chrome = text_masks[0] + text_masks[1]
    regions = panel_regions(details[3])
    light = Counter(target[index] for index in chrome).most_common(1)[0][0] > 128
    require(not sort_panel(target, regions, light), 'reference_already_shows_panel')

    def matches(pixels, expected, indices):
        return all(pixels[index] == expected[index] for index in indices)

    before = [pixels for _, end, pixels in rows if end < down_before]
    require(len(before) >= 2 and all(matches(pixels, idle, text) for pixels in before), 'pretrigger_stock_mismatch')
    first_shelf = None
    first_response = None
    response_scan_start = None
    pending = None
    for start, end, pixels in rows:
        panel = sort_panel(pixels, regions, light) and matches(pixels, target, chrome)
        shelf = not panel and matches(pixels, target, text)
        if start <= up_after:
            require(not (panel or shelf), 'target_before_card_release')
            continue
        if panel:
            require(first_shelf is not None, 'panel_before_visible_shelf')
            if pending is None:
                pending = start, end
            elif first_response is None:
                response_scan_start, first_response = pending
        else:
            pending = None
            if first_shelf is None and shelf:
                first_shelf = end
    require(first_shelf is not None, 'visible_library_not_observed')
    require(first_response is not None, 'sort_panel_response_not_observed')
    return {'scope': 'manual_sort_visit_framebuffer_observation',
            'down_before_us': down_before, 'down_after_us': down_after,
            'up_before_us': up_before, 'up_after_us': up_after,
            'first_visible_observed_us': first_shelf, 'first_response_observed_us': first_response,
            'response_scan_start_us': response_scan_start,
            'visible_upper_ms': (first_shelf - up_before) / 1000,
            'ready_upper_ms': (first_response - up_before) / 1000,
            'first_response_is_upper_bound': True, 'observer': diagnostics}


def analyze_visit(rule_path, trace_path, stock_path, brackets, touch_bytes):
    if not isinstance(touch_bytes, bytes) or len(touch_bytes) > 200:
        raise ValueError('single_physical_touch_not_observed')
    witness = re.fullmatch(rb'ATW1 SINGLE' + rb' ([1-9][0-9]{0,19})' * 7 + rb'\n', touch_bytes)
    if witness is None:
        raise ValueError('single_physical_touch_not_observed')
    window_start, window_end, press_lo, press_hi, release_lo, release_hi, samples = map(int, witness.groups())
    result = analyze(rule_path, trace_path, stock_path, brackets)
    observer.require(samples >= 3 and window_start < result['down_before_us']
                     and result['up_after_us'] < press_lo <= press_hi <= release_hi <= window_end
                     and press_lo <= release_lo <= release_hi
                     and max(press_hi - press_lo, release_hi - release_lo) <= observer.GAP,
                     'touch_not_bound_to_visit')
    observer.require(result['first_visible_observed_us'] <= press_lo and press_hi <= result['response_scan_start_us']
                     and release_hi <= result['first_response_observed_us'] <= window_end,
                     'shelf_touch_panel_order_not_verified')
    result.update(scope='stock_card_release_to_visible_interactive_library_upper_bound',
                  physical_sort_tap_observed=True, cover_completion_required=False,
                  visible_to_touch_upper_ms=(press_hi - result['first_visible_observed_us']) / 1000)
    return result
