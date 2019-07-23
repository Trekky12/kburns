import argparse
import os
import itertools
import random
import copy
import subprocess
from PIL import Image

ffmpeg = "ffmpeg.exe"
ffprobe = "ffprobe.exe"

output_width = 1280
output_height = 800
slide_duration_s = 4
fade_duration_s = 1
fps = 60
zoom_rate = 0.1
zoom_direction = "random"
scale_mode = "auto"
dump_filter_graph = False
loopable = False
audio = None
overwrite = False

parser = argparse.ArgumentParser()

parser.add_argument("-s", "--size", metavar='WIDTHxHEIGHT', help="Output width (default: %sx%s)" %(output_width, output_height))
parser.add_argument("-sd", "--slide-duration", metavar='DURATION', type=float, help="Slide duration (seconds) (default: %s)" %(slide_duration_s))
parser.add_argument("-fd", "--fade-duration", metavar='DURATION', type=float, help="Fade duration (seconds) (default: %s)" %(fade_duration_s))
parser.add_argument("-fps", "--fps", metavar='FPS', type=int, help="Output framerate (frames per second) (default: %s)" %(fps))

zoom_direction_possibilities = [["top", "center", "bottom"], ["left", "center", "right"], ["in", "out"]]
zoom_direction_choices = ["random"] + list(map(lambda x: "-".join(x), itertools.product(*zoom_direction_possibilities)))
parser.add_argument("-zd", "--zoom-direction", metavar='DIRECTION', choices=zoom_direction_choices, help="Zoom direction (default: %s)" %(zoom_direction))

parser.add_argument("-zr", "--zoom-rate", metavar='RATE', type=float, help="Zoom rate (default:  %s)" %(zoom_rate))
parser.add_argument("-sm", "--scale-mode", metavar='SCALE_MODE', choices=["auto", "pad", "pan", "crop_center"], help="Scale mode (pad, crop_center, pan) (default: %s)" %(scale_mode))
parser.add_argument("-dump", "--dump-filter-graph", action='store_true', help="Dump filter graph to '<OUTPUT>.filtergraph.png' for debugging")
parser.add_argument("-l", "--loopable", action='store_true', help="Create loopable video")
parser.add_argument("-a", "--audio", metavar='FILE', help="Use FILE as audio track")
parser.add_argument("-y", action='store_true', help="Overwrite output file without asking")

parser.add_argument("input_files", nargs='*')
parser.add_argument("output_file")

args = parser.parse_args()
    
if args.size:
    size = args.size.split("x")
    output_width = int(size[0])
    output_height = int(size[1])
 
if args.slide_duration: 
    slide_duration_s = args.slide_duration

if args.fade_duration: 
    fade_duration_s = args.fade_duration    
    
if args.fps: 
    fps = args.fps    

if args.zoom_direction:
    zoom_direction = args.zoom_direction       
    
if args.zoom_rate:
    zoom_rate = args.zoom_rate    
    
if args.scale_mode:
    scale_mode = args.scale_mode
    
dump_filter_graph = args.dump_filter_graph    
loopable = args.loopable

if args.audio:
    audio = args.audio

overwrite = args.y    
   
if zoom_direction == "random":
    x_directions = ["left", "right"]
    y_directions = ["top", "bottom"]
    z_directions = ["in", "out"]
else:
    x_directions = [zoom_direction.split("-")[1]]
    y_directions = [zoom_direction.split("-")[0]]
    z_directions = [zoom_direction.split("-")[2]]


IMAGE_EXTENSIONS = ["jpg", "jpeg", "png"]
VIDEO_EXTENSIONS = ["mp4", "mpg", "avi"]

output_ratio = output_width / output_height
last_offset_s = 0

# workaround a float bug in zoompan filter that causes a jitter/shake
# https://superuser.com/questions/1112617/ffmpeg-smooth-zoompan-with-no-jiggle/1112680#1112680
# https://trac.ffmpeg.org/ticket/4298
supersample_width = output_width*4
supersample_height = output_height*4

slides = []
for input in args.input_files:

    extension = input.split(".")[-1]
    
    if extension in VIDEO_EXTENSIONS:
        duration = subprocess.check_output("%s -show_entries format=duration -v error -of default=noprint_wrappers=1:nokey=1 %s" %(ffprobe, input))
        
        slide = {}
        slide["video"] = True
        slide["file"] = input
        slide["duration_s"] = float(duration)
        slide["fade_duration_s"] = fade_duration_s
        slide["offset_s"] = last_offset_s

        slides.append(slide)
            
        # calculate next offset
        last_offset_s = last_offset_s + (slide["duration_s"] - slide["fade_duration_s"])

  
    elif extension in IMAGE_EXTENSIONS:
        im = Image.open(input)
        width, height = im.size
        ratio = width / height
        
        slide = {}
        slide["file"] = input
        slide["width"] = width
        slide["height"] = height

        if scale_mode == "auto":
            slide["scale"] = "pad" if abs(ratio - output_ratio) > 0.5 else "crop_center"
        else:
            slide["scale"] = scale_mode
        
        slide["direction_x"] = random.choice(x_directions)
        slide["direction_y"] = random.choice(y_directions)
        slide["direction_z"] = random.choice(z_directions)
        slide["video"] = False
        slide["duration_s"] = slide_duration_s
        slide["fade_duration_s"] = fade_duration_s
        slide["offset_s"] = last_offset_s
        
        slides.append(slide)
        
        # calculate next offset
        last_offset_s = last_offset_s + (slide["duration_s"] - slide["fade_duration_s"])
        
    
    
if loopable:
    first_slide = copy.copy(slides[0])
    first_slide["offset_s"] = last_offset_s
    slides.append(first_slide)

# Calculate total duration
total_duration = sum([slide["duration_s"]  - slide["fade_duration_s"] for slide in slides])+slides[-1]["fade_duration_s"]

# Base black image
filter_chains = [
  "color=c=black:r=%s:size=%sx%s:d=%s[black]" %(fps, output_width, output_height, total_duration)
]

# create zoom/pan effect of images
for slide in [slide for slide in slides if slide["video"] is not True]:
    slide_filters = ["format=pix_fmts=yuva420p"]

    ratio = slide["width"]/slide["height"]
    
    # Crop to make video divisible
    slide_filters.append("crop=w=2*floor(iw/2):h=2*floor(ih/2)")
    
    # Pad filter
    if slide["scale"] == "pad" or slide["scale"] == "pan":
        width, height = [slide["width"], int(slide["width"]/output_ratio)] if ratio > output_ratio else [int(slide["height"]*output_ratio), slide["height"]]
        slide_filters.append("pad=w=%s:h=%s:x='(ow-iw)/2':y='(oh-ih)/2'" %(width, height))
        
    # Zoom/pan filter
    z_step = zoom_rate/(fps*slide_duration_s)
    z_rate = zoom_rate
    z_initial = 1
    x = 0
    y = 0
    z = 0
    if slide["scale"] == "pan":
        z_initial = ratio/output_ratio
        z_step = z_step*ratio/output_ratio
        z_rate = z_rate*ratio/output_ratio
        if ratio > output_ratio:
            if (slide["direction_x"] == "left" and slide["direction_z"] != "out") or (slide["direction_x"] == "right" and slide["direction_z"] == "out"):
                x = "(1-on/%s*%s))*(iw-iw/zoom)" %(fps, slide_duration_s)
            elif (slide["direction_x"] == "right" and slide["direction_z"] != "out") or (slide["direction_x"] == "left" and slide["direction_z"] == "out"):
                x = "(on/(%s*%s))*(iw-iw/zoom)" %(fps, slide_duration_s)
            else:
                x = "(iw-ow)/2"
                
            y_offset = "(ih-iw/%s)/2" %(ratio)

            if slide["direction_y"] == "top":
                y = y_offset
            elif slide["direction_y"] == "center":
                y = "%s+iw/%s/2-iw/%s/zoom/2" %(y_offset, ratio, output_ratio)
            elif slide["direction_y"] == "bottom":
                y = "%s+iw/%s-iw/%s/zoom" %(y_offset, ratio, output_ratio)
        
        else:
            z_initial = output_ratio/ratio
            z_step = z_step*output_ratio/ratio
            z_rate = z_rate*output_ratio/ratio
            x_offset = "(iw-%s*ih)/2" %(ratio)
            
            if slide["direction_x"] == "left":
                x = x_offset
            elif slide["direction_x"] == "center":
                x = "%s+ih*%s/2-ih*%s/zoom/2" %(x_offset, ratio, output_ratio)
            elif slide["direction_x"] == "right":
                x = "%s+ih*%s-ih*%s/zoom" %(x_offset, ratio, output_ratio)
            
            if (slide["direction_y"] == "top" and slide["direction_z"] != "out") or (slide["direction_y"] == "bottom" and slide["direction_z"] == "out"):
                y = "(1-on/(%s*%s))*(ih-ih/zoom)" %(fps, slide_duration_s)
            elif (slide["direction_y"] == "bottom" and slide["direction_z"] != "out") or (slide["direction_y"] == "top" and slide["direction_z"] == "out"):
                y = "(on/(%s*%s))*(ih-ih/zoom)" %(fps, slide_duration_s)
            else:
                y = "(ih-oh)/2"
    else:
        if slide["direction_x"] == "left":
            x = 0
        elif slide["direction_x"] == "center":
            x = "iw/2-(iw/zoom/2)"
        elif slide["direction_x"] == "right":
            x = "iw-iw/zoom"
    
        if slide["direction_y"] == "top":
            y = 0
        elif slide["direction_y"] == "center":
            y = "ih/2-(ih/zoom/2)"
        elif slide["direction_y"] == "bottom":
            y = "ih-ih/zoom"
    
    
    if slide["direction_z"] == "in":
        z = "if(eq(on,1),%s,zoom+%s)" %(z_initial, z_step)
    elif slide["direction_z"] == "out":
      "if(eq(on,1),%s,zoom-%s)" %(z_initial+z_rate, z_step)

      
    width = 0
    height = 0
    if slide["scale"] == "crop_center":
        if output_ratio > ratio:
            width, height = [output_width, int(output_width/ratio)]
        else:
            width, height = [int(output_height*ratio), output_height]
    if slide["scale"] == "pan" or slide["scale"] == "pad":
        width, height = [output_width, output_height]

    slide_filters.append("scale=%sx%s,zoompan=z='%s':x='%s':y='%s':fps=%s:d=%s*%s:s=%sx%s" %(supersample_width, supersample_height, z, x, y, fps, fps, slide_duration_s, width, height))
    
    # Crop filter
    if slide["scale"] == "crop_center":
        crop_x = "(iw-ow)/2"
        crop_y = "(ih-oh)/2"
        slide_filters.append("crop=w=%s:h=%s:x='%s':y='%s'" %(output_width, output_height, crop_x, crop_y))
        
    # save the filters for rendering
    slide["filters"] = slide_filters

    
for i, slide in enumerate(slides):    
    filters = []
    
    # include the ken-burns effect image filters
    if not slide["video"]:
        filters.extend(slide["filters"])
    # scale videos
    else:
        filters.append("scale=w=%s:h=-1" %(output_width))
        
    # Fade filter   
    if slide["fade_duration_s"] > 0:
        filters.append("fade=t=in:st=0:d=%s:alpha=%s" %(slide["fade_duration_s"], 0 if i == 0 else 1))
        filters.append("fade=t=out:st=%s:d=%s:alpha=%s" %(slide["duration_s"]-slide["fade_duration_s"], slide["fade_duration_s"], 0 if i == len(slides) - 1 else 1))
  
    # Time
    filters.append("setpts=PTS-STARTPTS+%s/TB" %(slide["offset_s"]))

    # All together now
    filter_chains.append("[%s:v]" %(i) + ", ".join(filters) + "[v%s]" %(i)) 
    

for i, slide in enumerate(slides):
    input_1 = "ov%s" %(i-1) if i > 0 else "black"
    input_2 = "v%s" %(i)
    output = "out" if i == len(slides) - 1 else "ov%s" %(i)
    overlay_filter = "overlay" + ("=format=yuv420" if i == len(slides) - 1 else "")
    
    filter_chains.append("[%s][%s]%s[%s]" %(input_1, input_2, overlay_filter, output))


# Run ffmpeg
cmd = [ ffmpeg, "-hide_banner", 
        "-y" if overwrite else "",
        # slides
        " ".join(["-i %s" %(slide["file"]) for slide in slides]),
        "-i %s" %(audio) if audio else "",
        # filters
        "-filter_complex \"%s\"" % (";".join(filter_chains)),
        # define duration
        # if video should be loopable, skip the start fade-in (-ss) and the end fade-out (video is stopped after the fade-in of the last image which is the same as the first-image)
        "-ss %s -t %s" %(slides[0]["fade_duration_s"], sum([slide["duration_s"]  - slide["fade_duration_s"] for slide in slides[:-1]])) if loopable else "-t %s" %(total_duration),
        # define output
        "-map", "[out]",
        # audio is last input, use this for audio
        "-map %s:a" %(len(slides)) if audio else "",
        "-c:v", "libx264", args.output_file
]

os.system(" ".join(cmd))