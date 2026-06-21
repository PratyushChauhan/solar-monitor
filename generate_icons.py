#!/usr/bin/env python3
"""Generate PNG icons for PWA"""
from PIL import Image, ImageDraw, ImageFont
import os

def create_icon(size, output_path):
    """Create a solar-themed PNG icon."""
    img = Image.new('RGBA', (size, size), (15, 23, 42, 255))  # #0f172a
    draw = ImageDraw.Draw(img)
    
    # Draw sun rays
    center = size // 2
    ray_length = size // 3
    ray_count = 12
    
    for i in range(ray_count):
        angle = (360 / ray_count) * i
        import math
        rad = math.radians(angle)
        x1 = center + int((size // 5) * math.cos(rad))
        y1 = center + int((size // 5) * math.sin(rad))
        x2 = center + int(ray_length * math.cos(rad))
        y2 = center + int(ray_length * math.sin(rad))
        draw.line([(x1, y1), (x2, y2)], fill=(250, 204, 21, 255), width=max(2, size // 48))
    
    # Draw sun center
    sun_radius = size // 6
    draw.ellipse(
        [(center - sun_radius, center - sun_radius), 
         (center + sun_radius, center + sun_radius)],
        fill=(250, 204, 21, 255)
    )
    
    # Save as PNG
    img.save(output_path, 'PNG')
    print(f"Created {output_path} ({size}x{size})")

if __name__ == '__main__':
    base_dir = os.path.dirname(os.path.abspath(__file__))
    
    create_icon(192, os.path.join(base_dir, 'icon-192.png'))
    create_icon(512, os.path.join(base_dir, 'icon-512.png'))
    print("Done!")
