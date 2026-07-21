#!/usr/bin/env python3
"""
Ruzname: Image Generator (`generate.py`)
======================================
This script runs on your computer to fetch daily prayer times from the
Muwaqqit API and pre-render calendar images (YYYY-MM-DD.png) using PIL (Pillow)
1-bit text masks for the Pimoroni Inky Frame 7.3".
"""

import os
import time
import json
import argparse
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from PIL import Image, ImageDraw, ImageFont

# Color tuples corresponding to the palette colors
COLOR_BLACK  = (0, 0, 0)
COLOR_WHITE  = (255, 255, 255)
COLOR_GREEN  = (0, 255, 0)
COLOR_BLUE   = (0, 0, 255)
COLOR_RED    = (255, 0, 0)
COLOR_YELLOW = (255, 255, 0)
COLOR_ORANGE = (255, 165, 0)

# Define the palette with the specific colors for Inky (7-color e-ink display)
palette = list(COLOR_BLACK + COLOR_WHITE + COLOR_GREEN + COLOR_BLUE + COLOR_RED + COLOR_YELLOW + COLOR_ORANGE)

HIJRI_MONTHS = [
    "Muharram", "Safar", "Rabiul Awwal", "Rabiul Thani", "Jumadal Ula", 
    "Jumadal Akhir", "Rajab", "Shaban", "Ramadan", "Shawwal", "Dhul Qadah", "Dhul Hijjah"
]

# Configuration Constants
OUTPUT_DIR = "muwaqqit"
FONT_PATH = "PPNeueBit-Bold.otf"

# Muwaqqit URL (defaults to Buckingham Palace coordinates; generate yours at muwaqqit.com)
MUWAQQIT_URL = "https://www.muwaqqit.com/index?diptype=apparent&dn=&ea=-20.0&eh=0.0&eo=14&era=-17.0&fa=-19.0&fea=1.0&ia=4.5&isn=-10.0&isna=1.0&k=0.155&ln=-0.1419&lt=51.5014&p=1010.0&q=&rsa=1.0&t=15.0&tz=Europe%2FLondon&tztype=auto&vc=5.65&z=6&zt=1.0&suntype=limb"

def create_palette_image(palette):
    """Create a 1x1 palette image with the given palette."""
    palette_image = Image.new("P", (1, 1))
    extended_palette = palette + [0, 0, 0] * (256 - len(palette) // 3)
    palette_image.putpalette(extended_palette)
    return palette_image

def get_time_info(dt_str, query_date, offset=0):
    """Parse API datetime string, apply offset, and return formatted 12h time and next-day flag."""
    if not dt_str:
        return "", False
    
    # Parse the string
    dt = None
    for fmt, length in [("%Y-%m-%d %H:%M", 16), ("%Y-%m-%d %H:%M:%S", 19)]:
        try:
            dt = datetime.strptime(dt_str[:length], fmt)
            break
        except ValueError:
            continue
            
    if not dt:
        return "", False
        
    # Apply offset
    if offset:
        dt += timedelta(minutes=offset)
        
    # Format to 12-hour H:MM
    h = dt.hour % 12
    if h == 0:
        h = 12
    val_str = f"{h}:{dt.minute:02d}"
    
    # Check if next day
    is_nd = dt.date() > query_date.date()
    
    return val_str, is_nd

def format_angle(val):
    """Format astronomical angles to 1 decimal place (or 0 if integer)."""
    if val is None:
        return ""
    try:
        val_f = float(val)
        if val_f % 1 == 0:
            return f"{int(val_f)}"
        return f"{val_f:.1f}"
    except Exception:
        return str(val)

def safe_float(val):
    try:
        return float(val)
    except (TypeError, ValueError):
        return None

def safe_int(val, default=1):
    try:
        return int(val)
    except (TypeError, ValueError):
        return default

def label_with_angle(base, angle, prefix=" "):
    if angle is not None:
        formatted = format_angle(angle)
        if formatted:
            return f"{base}{prefix}{formatted}°"
    return base

def fetch_data(date_str):
    """Fetch prayer times from the Muwaqqit API for a given date."""
    parsed = urllib.parse.urlparse(MUWAQQIT_URL.strip())
    path = parsed.path
    if not path or path == "/" or path.endswith("/index") or path.endswith("/index.html"):
        path = "/api2.json"
    elif not path.endswith("/api2.json"):
        path = path.rstrip("/") + "/api2.json"

    query_params = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    query_params['d'] = [date_str]
    new_query = urllib.parse.urlencode(query_params, doseq=True)
    
    scheme = parsed.scheme if parsed.scheme else "https"
    netloc = parsed.netloc if parsed.netloc else "www.muwaqqit.com"
    url = urllib.parse.urlunparse((scheme, netloc, path, parsed.params, new_query, parsed.fragment))

    print(f"Fetching data for {date_str}...")
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        })
        with urllib.request.urlopen(req) as response:
            return json.loads(response.read().decode('utf-8'))
    except Exception as e:
        print(f"Failed to fetch data from API: {e}")
        return None

def main():
    parser = argparse.ArgumentParser(description="Generate daily prayer times images for Inky Frame.")
    parser.add_argument("--start", type=str, help="Start date (YYYY-MM-DD). Defaults to today.")
    parser.add_argument("--end", type=str, help="End date (YYYY-MM-DD). Defaults to start date.")
    args = parser.parse_args()
    
    # Determine date range
    if args.start:
        try:
            start_date = datetime.strptime(args.start, "%Y-%m-%d")
        except ValueError:
            print("Error: --start must be in YYYY-MM-DD format.")
            return
    else:
        start_date = datetime.now()
        
    if args.end:
        try:
            end_date = datetime.strptime(args.end, "%Y-%m-%d")
        except ValueError:
            print("Error: --end must be in YYYY-MM-DD format.")
            return
    else:
        end_date = start_date

    # Normalize dates to midnight
    start_date = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
    end_date = end_date.replace(hour=0, minute=0, second=0, microsecond=0)
    
    if end_date < start_date:
        print("Error: --end date must be on or after --start date.")
        return
        
    # Enforce a maximum date range of 365 days to prevent overwhelming the Muwaqqit API
    if (end_date - start_date).days > 365:
        print("Error: Date range exceeds 1 year (365 days). There is a maximum date range of 1 year to prevent overwhelming the Muwaqqit service.")
        return

    delta = timedelta(days=1)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    # Load fonts (using perfect 28px size, and 56px for dates)
    try:
        font = ImageFont.truetype(FONT_PATH, 28)
        font_date = ImageFont.truetype(FONT_PATH, 56)
        print(f"Loaded font '{FONT_PATH}' successfully.")
    except Exception as e:
        print(f"Error: Critical dependency missing. Failed to load custom font '{FONT_PATH}': {e}")
        return

    current_date = start_date
    while current_date <= end_date:
        date_str = current_date.strftime('%Y-%m-%d')
        
        # 1. Fetch API Data
        data = fetch_data(date_str)
        if not data:
            print(f"Skipping {date_str} due to fetch error.")
            current_date += delta
            continue
            
        # 2. Extract and format times/angles
        fa = safe_float(data.get('fa'))
        fea = safe_float(data.get('fea'))
        
        if fa is not None and fea is not None:
            imsak_angle = fa - fea
            fajr_angle = fa + fea
        else:
            imsak_angle = None
            fajr_angle = None
            
        weekday_str = current_date.strftime("%A")
        gregorian_str = f"{current_date.day} {current_date.strftime('%B %Y')}"
        hijri_month_num = max(1, min(12, safe_int(data.get('islamic_month_number'), 1)))
        hijri_str = f"{data.get('islamic_day', '')} {HIJRI_MONTHS[hijri_month_num - 1]} {data.get('islamic_year', '')}"
        
        # 3. Create canvas (480x800 portrait)
        img = Image.new("RGB", (480, 800), COLOR_WHITE)
        draw = ImageDraw.Draw(img)
        
        # Helper to build time rows with next-day detection
        def time_row(label, key, fallback_key=None, offset=0, color=COLOR_BLACK):
            dt_val = data.get(key)
            if not dt_val and fallback_key:
                dt_val = data.get(fallback_key)
            val, is_nd = get_time_info(dt_val, current_date, offset)
            return ("time", label, val, color, is_nd)

        # Define table rows
        # Format: (type, label, value, color, is_next_day)
        rows = [
            ("date", weekday_str, "", COLOR_WHITE, False),
            ("date", gregorian_str, "", COLOR_WHITE, False),
            time_row("Zuhr", "zohr_shadow", offset=1),
            time_row("Asr Awwal", "asr_shafi", offset=1),
            time_row("Asr Thani", "asr_hanafi", offset=1),
            time_row(label_with_angle("Karaha", data.get('ia')), "asr_makrooh"),
            time_row("Sunset", "sunset", offset=1),
            ("date", hijri_str, "", COLOR_RED, False),
            time_row(label_with_angle("Maghrib", data.get('rsa'), " -"), "sunset_safety_plus", offset=1),
            time_row(label_with_angle("Ishtibak", data.get('isn')), "ishtibak"),
            time_row(label_with_angle("Isha Awwal", data.get('era')), "esha_red", offset=1),
            time_row(label_with_angle("Isha Thani", data.get('ea')), "esha", offset=1),
            time_row("Half Shari Night", "half_night"),
            ("blank", "", "", COLOR_BLACK, False),
            time_row(label_with_angle("Imsak", imsak_angle), "fajr_t_safety_minus", fallback_key="anti_transit_t"),
            time_row(label_with_angle("Fajr", fajr_angle), "fajr_t_safety_plus", fallback_key="anti_transit_t", offset=1),
            time_row("Sunrise", "sunrise_t"),
            time_row(label_with_angle("Duha", data.get('ia')), "ishraq_t", offset=1),
        ]
        
        y_top = 55
        # Set header background color: Red for weekdays (Mon-Fri), Blue for weekends (Sat-Sun)
        is_weekend = current_date.weekday() >= 5
        header_bg_color = COLOR_BLUE if is_weekend else COLOR_RED
        # Draw solid header background rectangle covering the full top banner
        draw.rectangle([(0, 0), (480, y_top + 101)], fill=header_bg_color)
        
        font_ascent = font.getmetrics()[0]
        font_date_ascent = font_date.getmetrics()[0]

        for i, row in enumerate(rows):
            row_type, label, value, color, is_nd = row
            target_categories = ["Zuhr", "Asr Thani", "Maghrib", "Isha Awwal", "Fajr", "Sunrise"]
            has_circle = any(cat in label for cat in target_categories) if row_type == "time" else False
            if row_type == "date":
                if i == 0:
                    row_height = 38
                elif i == 1:
                    row_height = 54
                else:
                    row_height = 40
            else:
                row_height = 32
            
            if row_type == "date":
                # First two date rows (weekday, date) use font_date, others use font
                active_font = font_date if i in (0, 1) else font
                active_ascent = font_date_ascent if i in (0, 1) else font_ascent
                bbox = active_font.getbbox(label)
                w = bbox[2] - bbox[0]
                h = bbox[3] - bbox[1]
                if w > 0 and h > 0:
                    x_paste = 60 + (360 - w) // 2
                    y_paste = y_top + (row_height - active_ascent) // 2
                    if i == 1:
                        y_paste -= 1  # Shift second title line 1px up for extra bottom padding
                    
                    text_img = Image.new("1", (w, h), 0)
                    text_draw = ImageDraw.Draw(text_img)
                    text_draw.text((-bbox[0], -bbox[1]), label, font=active_font, fill=1)
                    img.paste(color, (x_paste, y_paste), mask=text_img)
                
            elif row_type == "time":
                if has_circle:
                    dot_size = 8
                    dot_x = 44
                    dot_y = y_top + (row_height - dot_size) // 2
                    draw.ellipse((dot_x, dot_y, dot_x + dot_size, dot_y + dot_size), fill=COLOR_RED)
                    
                # Left label using 1-bit mask
                bbox_lbl = font.getbbox(label)
                w_lbl = bbox_lbl[2] - bbox_lbl[0]
                h_lbl = bbox_lbl[3] - bbox_lbl[1]
                if w_lbl > 0 and h_lbl > 0:
                    if "°" in label and " " in label:
                        parts = label.rsplit(" ", 1)
                        base_text = parts[0]
                        angle_text = parts[1]
                        
                        bbox_base = font.getbbox(base_text)
                        w_base = bbox_base[2] - bbox_base[0]
                        h_base = bbox_base[3] - bbox_base[1]
                        if w_base > 0 and h_base > 0:
                            y_base = y_top + (row_height - font_ascent) // 2
                            text_img_base = Image.new("1", (w_base, h_base), 0)
                            text_draw_base = ImageDraw.Draw(text_img_base)
                            text_draw_base.text((-bbox_base[0], -bbox_base[1]), base_text, font=font, fill=1)
                            img.paste(color, (60, y_base), mask=text_img_base)
                            
                        bbox_angle = font.getbbox(angle_text)
                        w_angle = bbox_angle[2] - bbox_angle[0]
                        h_angle = bbox_angle[3] - bbox_angle[1]
                        if w_angle > 0 and h_angle > 0:
                            y_angle = y_top + (row_height - font_ascent) // 2
                            text_img_angle = Image.new("1", (w_angle, h_angle), 0)
                            text_draw_angle = ImageDraw.Draw(text_img_angle)
                            text_draw_angle.text((-bbox_angle[0], -bbox_angle[1]), angle_text, font=font, fill=1)
                            img.paste(COLOR_RED, (60 + w_base + 8, y_angle), mask=text_img_angle)
                    else:
                        y_paste_lbl = y_top + (row_height - font_ascent) // 2
                        text_img_lbl = Image.new("1", (w_lbl, h_lbl), 0)
                        text_draw_lbl = ImageDraw.Draw(text_img_lbl)
                        text_draw_lbl.text((-bbox_lbl[0], -bbox_lbl[1]), label, font=font, fill=1)
                        img.paste(color, (60, y_paste_lbl), mask=text_img_lbl)
                
                # Right value using 1-bit mask
                bbox_val = font.getbbox(value)
                w_val = bbox_val[2] - bbox_val[0]
                h_val = bbox_val[3] - bbox_val[1]
                if w_val > 0 and h_val > 0:
                    x_paste_val = 420 - w_val
                    y_paste_val = y_top + (row_height - font_ascent) // 2
                    
                    text_img_val = Image.new("1", (w_val, h_val), 0)
                    text_draw_val = ImageDraw.Draw(text_img_val)
                    text_draw_val.text((-bbox_val[0], -bbox_val[1]), value, font=font, fill=1)
                    img.paste(color, (x_paste_val, y_paste_val), mask=text_img_val)
                    
                    if is_nd:
                        # Draw red triangle (size 8x8) to the right of the column, outside the grid
                        dot_size = 8
                        dot_x = 428
                        dot_y = y_top + (row_height - dot_size) // 2
                        draw.polygon([
                            (dot_x + dot_size // 2, dot_y),
                            (dot_x, dot_y + dot_size),
                            (dot_x + dot_size, dot_y + dot_size)
                        ], fill=COLOR_RED)
                    
            # Draw row border if not the last row and not within/after the header (index 0 and 1)
            if i < len(rows) - 1 and i != 0 and i != 1:
                draw.line([(60, y_top + row_height), (420, y_top + row_height)], fill=COLOR_BLACK, width=1)
                
            y_top += row_height
            if i == 1:
                y_top += 26



        # 4. Lossless rotation (exactly -90 degrees using transpose)
        # Image.ROTATE_270 rotates 270 degrees clockwise, which is equivalent to -90 degrees counter-clockwise
        rotated_image = img.transpose(Image.ROTATE_270)
        
        # 5. Quantize image to Inky Palette
        rgb_image = rotated_image.convert('RGB')
        
        try:
            none_dither = Image.DITHER.NONE
        except AttributeError:
            none_dither = Image.NONE
            
        # Step 1: Quantize to clean 4-color palette to lock solid pixels (Black, White, Blue, Red)
        four_color_palette = list(COLOR_BLACK + COLOR_WHITE + COLOR_BLUE + COLOR_RED)
        four_color_palette_image = create_palette_image(four_color_palette)
        clean_4color_image = rgb_image.quantize(palette=four_color_palette_image, dither=none_dither)
        
        # Step 2: Quantize to final 7-color palette
        rgb_4color = clean_4color_image.convert('RGB')
        palette_image = create_palette_image(palette)
        output_image = rgb_4color.quantize(palette=palette_image, dither=none_dither)
        
        # Save output image
        output_image_path = os.path.join(OUTPUT_DIR, f'{date_str}.png')
        output_image.save(output_image_path)
        print(f"Saved daily image to: {output_image_path}")

        current_date += delta
        # 1-second pause between requests to be respectful to the public Muwaqqit API server and avoid IP rate-limiting/throttling
        time.sleep(1)
    print("Done")

if __name__ == "__main__":
    main()
