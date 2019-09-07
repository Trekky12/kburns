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
loopable = False
overwrite = False
generate_temp = False
delete_temp = False

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
parser.add_argument("-l", "--loopable", action='store_true', help="Create loopable video")
parser.add_argument("-y", action='store_true', help="Overwrite output file without asking")
parser.add_argument("-t", "--temp", action='store_true', help="Generate temporary files")
parser.add_argument("-d", "--delete-temp", action='store_true', help="Generate temporary files")

parser.add_argument("input_files", nargs='*')
parser.add_argument("output_file")

args = parser.parse_args()
    
if args.size is not None:
    size = args.size.split("x")
    output_width = int(size[0])
    output_height = int(size[1])
 
if args.slide_duration is not None: 
    slide_duration_s = args.slide_duration

if args.fade_duration is not None: 
    fade_duration_s = args.fade_duration    
    
if args.fps is not None: 
    fps = args.fps    

if args.zoom_direction is not None:
    zoom_direction = args.zoom_direction       
    
if args.zoom_rate is not None:
    zoom_rate = args.zoom_rate    
    
if args.scale_mode is not None:
    scale_mode = args.scale_mode
      
loopable = args.loopable

overwrite = args.y    
generate_temp = args.temp
delete_temp = args.delete_temp
   
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
AUDIO_EXTENSIONS = ["mp3", "ogg", "flac"]

output_ratio = output_width / output_height
last_offset_s = 0

# workaround a float bug in zoompan filter that causes a jitter/shake
# https://superuser.com/questions/1112617/ffmpeg-smooth-zoompan-with-no-jiggle/1112680#1112680
# https://trac.ffmpeg.org/ticket/4298
supersample_width = output_width*4
supersample_height = output_height*4

slides = []
audio = []
for input in args.input_files:

    extension = input.split(".")[-1]
    
    if extension in VIDEO_EXTENSIONS:
        duration = subprocess.check_output("%s -show_entries format=duration -v error -of default=noprint_wrappers=1:nokey=1 %s" %(ffprobe, input))
        has_audio = subprocess.check_output("%s -select_streams a -show_entries stream=codec_type -v error -of default=noprint_wrappers=1:nokey=1 %s" %(ffprobe, input))
        
        slide = {}
        slide["video"] = True
        slide["file"] = input
        slide["duration_s"] = float(duration)
        slide["has_audio"] = "audio" in str(has_audio)
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
        
    elif extension in AUDIO_EXTENSIONS:
        audio.append(input)
    
    
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

# =====================================    
#       IMAGES
# =====================================    

tempfiles = []
# create zoom/pan effect of images
for i, slide in enumerate([slide for slide in slides if slide["video"] is not True]):
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
        z = "if(eq(on,1),%s,zoom-%s)" %(z_initial+z_rate, z_step)

      
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
        

    # Generate temp video with Ken Burns effect
    if generate_temp:
        slide["tempvideo"] = "temp-kburns-%s.mp4" %(i)
        cmd = [
            ffmpeg, "-y", "-hide_banner", "-v", "quiet",
            "-i", slide["file"],
            "-filter_complex", ",".join(slide_filters),
            "-crf", "0" ,"-preset", "ultrafast", "-tune", "stillimage",
            "-c:v", "libx264", slide["tempvideo"]
        ]

        # re-use existing temp file
        if not os.path.exists(slide["tempvideo"]):
            subprocess.call(" ".join(cmd))

        slide["file"] = slide["tempvideo"]
        tempfiles.append(slide["tempvideo"])

    # or save the filters for rendering
    else:
        slide["filters"] = slide_filters

# =====================================    
#       IMAGE AND VIDEOS
# =====================================    
    
for i, slide in enumerate(slides):    
    filters = []
    
    # include the ken-burns effect image filters if no temporary videos where created
    if not slide["video"] and not generate_temp:
        filters.extend(slide["filters"])

    # scale video to fit the width
    if slide["video"]:
        filters.append("scale=w=%s:h=-1" %(output_width))
        
    # Fade filter   
    if slide["fade_duration_s"] > 0:
        filters.append("fade=t=in:st=0:d=%s:alpha=%s" %(slide["fade_duration_s"], 0 if i == 0 else 1))
        filters.append("fade=t=out:st=%s:d=%s:alpha=%s" %(slide["duration_s"]-slide["fade_duration_s"], slide["fade_duration_s"], 0 if i == len(slides) - 1 else 1))
    else:
        filters.append("tpad=stop_duration=%s:color=black" %(slide["duration_s"]))

    # Time
    filters.append("setpts=PTS-STARTPTS+%s/TB" %(slide["offset_s"]))

    # All together now
    filter_chains.append("[%s:v]" %(i) + ", ".join(filters) + "[v%s]" %(i)) 
    

for i, slide in enumerate(slides):
    input_1 = "ov%s" %(i-1) if i > 0 else "black"
    input_2 = "v%s" %(i)
    output = "out" if i == len(slides) - 1 else "ov%s" %(i)
    overlay_filter = "overlay" + ("=format=yuv420" if i == len(slides) - 1 else "")
    
    # center the video
    if slide["video"]:
        overlay_filter = "overlay=(W-w)/2:(H-h)/2"
    
    filter_chains.append("[%s][%s]%s[%s]" %(input_1, input_2, overlay_filter, output))
    
    
# =====================================    
#       AUDIO   
# =====================================    
# audio from video slides
audio_tracks = []
for i, slide in enumerate(slides):
    if slide["video"] and slide["has_audio"]:
        audio_tracks.append("[a%s]" %(i))
        
        filters = []
        # Fade music in filter
        if slide["fade_duration_s"] > 0:
            filters.append("afade=t=in:st=0:d=%s" %(slide["fade_duration_s"]))
            filters.append("afade=t=out:st=%s:d=%s" %(slide["duration_s"] - slide["fade_duration_s"], slide["fade_duration_s"] ))
        filters.append("adelay=%s|%s" %( int(slide["offset_s"]*1000), int(slide["offset_s"]*1000)))
        
        filter_chains.append("[%s:a] %s [a%s]" %(i, ",".join(filters), i))

# merge background tracks
music_input_offset = len(slides)
background_audio = ["[%s:a]" %(i+music_input_offset) for i, track in enumerate(audio)]
if len(background_audio) > 0:
    filter_chains.append("%s concat=n=%s:v=0:a=1[background_audio]" %("".join(background_audio), len(audio)))

    # extract background audio sections between videos
    background_sections = []
    # is it starting with a video or an image?
    section_start_slide = None if slides[0]["video"] else slides[0]
    for slide in slides:
        # is it a video and we have a start value => end of this section
        if slide["video"] and slide["has_audio"] and section_start_slide is not None:
            background_sections.append({ "start": section_start_slide["offset_s"], "fade_in": section_start_slide["fade_duration_s"], "end": slide["offset_s"], "fade_out": slide["fade_duration_s"] })
            section_start_slide = None
        # is it a image but the previous one was a video => start new section
        if not slide["video"] and section_start_slide is None:
            section_start_slide = slide

    # the last section is ending with an image => end of section is end generated video
    if section_start_slide is not None:
        background_sections.append({ "start": section_start_slide["offset_s"], "fade_in": section_start_slide["fade_duration_s"], "end": total_duration-slides[-1]["fade_duration_s"] })
        
    # split the background tracks into the necessary copies for the fades
    filter_chains.append("[background_audio]asplit=%s %s" %(len(background_sections), "".join(["[b%s]" %(i) for i, section in enumerate(background_sections)])))

    # fade background music in/out
    for i, section in enumerate(background_sections):
        audio_tracks.append("[b%sf]" %(i))
        filter_chains.append("[b%s]afade=t=in:st=%s:d=%s,afade=t=out:st=%s:d=%s[b%sf]" %(i, section["start"], fade_duration_s, section["end"], fade_duration_s, i))


# video audio and background sections should be merged 
if len(audio_tracks) > 0:    
    filter_chains.append("%s amix=inputs=%s[aout]" %("".join(audio_tracks), len(audio_tracks))) 

# =====================================    
#       FINAL VIDEO
# =====================================    

# Run ffmpeg
cmd = [ ffmpeg, "-hide_banner", 
        "-y" if overwrite else "",
        # slides
        " ".join(["-i %s" %(slide["file"]) for slide in slides]),
        " ".join(["-i %s" %(track) for track in audio]),
        # filters
        "-filter_complex \"%s\"" % (";".join(filter_chains)),
        # define duration
        # if video should be loopable, skip the start fade-in (-ss) and the end fade-out (video is stopped after the fade-in of the last image which is the same as the first-image)
        "-ss %s -t %s" %(slides[0]["fade_duration_s"], sum([slide["duration_s"]  - slide["fade_duration_s"] for slide in slides[:-1]])) if loopable else "-t %s" %(total_duration),
        # define output
        "-map", "[out]:v",
        "-c:v", "libx264",
        "-map [aout]:a" if len(audio_tracks) > 0 else "",
        # audio compression and bitrate
        "-c:a", "aac" if len(audio_tracks) > 0 else "", 
        "-b:a", "160k" if len(audio_tracks) > 0 else "", 
         args.output_file
]

subprocess.call(" ".join(cmd))

if delete_temp:
    for temp in tempfiles:
        os.remove(temp)