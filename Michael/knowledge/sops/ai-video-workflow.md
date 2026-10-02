# AI video creation workflow (HyperFrames, ComfyUI, ElevenLabs)

How Michael creates videos with AI using natural-language prompts instead of Premiere Pro: HyperFrames (HTML to video), ComfyUI (local image and video generation) and ElevenLabs (narration, transcripts, sound effects), coordinated by an AI assistant.

## Overview and who to ask

- **Owner / who to ask:** Michael, who built this workflow and presented it to coworkers. Ask Michael about the AI video workflow, the example projects, or getting set up.
- **Example projects:** PATH Demo (real plus AI-generated video), Lenovo Nerve (product reveal, entirely AI-generated, shots made locally with MiniMax H3 in ComfyUI), Lenovo Quality Month (event recap: real footage plus HTML animations). Each video took 1 to 2 days.
- **How it works in short:** you give a brief and your media; the AI coordinator audits the media, writes a video spec and storyboard, then calls ComfyUI (visuals), ElevenLabs (voice) and HyperFrames (editing, animation, rendering); you review clip by clip and ask for changes (slides 4 to 6).
- **Setup summaries:** HyperFrames (slide 7), ElevenLabs API key and voice ID (slide 10), ComfyUI with MiniMax H3 (slides 11 and 12). Links are on slide 13.

_The text below is a slide-by-slide conversion of the source deck AI-Video-Workflow.pptx (videos and pictures omitted; speaker notes included)._

## Slide 1: AI video

AI video
creation

Michael’s workflow with HyperFrames,
ComfyUI and ElevenLabs

**Speaker notes:**

A walkthrough for coworkers: what we made, what each tool does, and where the planning and review work happens. The examples come from our existing PATH Demo, Lenovo Nerve and Lenovo Quality Month projects.

## Slide 2: 3 Different Projects created with AI

PATH Demo

A whimsical demo mixing real
and AI-generated video.

Lenovo Nerve

A product reveal composed entirely of AI-generated content.

Lenovo Quality Month

An event recap composed entirely of real footage alongside HTML animations.

Natural language prompts. Premiere Pro never opened.

This presentation was also created entirely with AI

Each video took 1–2 days to create.

_Image: PATH Demo_

_Image: Lenovo Nerve_

_Image: Lenovo Quality Month_

**Speaker notes:**

each video took 1–2 days to create. Play one project at a time. Full videos are embedded. For a short presentation, show brief excerpts rather than all three in full. Explain that the project owner supplied footage, references and direction, then used natural language to guide assembly and revisions. The statement about no Premiere Pro use comes from the presenter's account of these projects. The deck itself was authored through code, without manually building slides in PowerPoint.

## Slide 3: Less manual production work

×

×

NO MORE
PREMIERE PRO

NO MORE BUILDING
SLIDES BY HAND

NO MORE MANUAL EDITING

**Speaker notes:**

The benefit is moving repetitive production work into prompts: cutting, captions, animation, voice revisions and rendering. We still make the creative decisions, provide accurate material, review the result and ask for changes. Avoid promising a one-prompt result or zero effort. These are examples of how this workflow reduced manual operation of editing software. Official tool logos are used only to identify the tools.

## Slide 4: You direct. AI creates.

You provide the direction

Describe your idea, audience and
style. Review the result and
explain what needs to change.

AI is the painter

Turns your instructions into a plan
and uses the tools to build it.

Media is the paint

Your images, footage and references
give AI material to work with.

Tools are the brushes

ComfyUI generates visuals. HyperFrames assembles and animates them.

_Image: Painter metaphor_

**Speaker notes:**

Think of the AI as a painter working to your direction. You provide the ideas, examples and media. Tools such as HyperFrames and ComfyUI give it ways to make the result. Specific descriptions and good references make your intent easier to follow. The metaphor is not a claim that technical limits disappear.

## Slide 5: The tools behind the workflow

Your brief

The audience, purpose and direction.

Your media

Photos, clips, music and other references.

AI coordinator

Organizes assets, writes the spec and calls tools.

ComfyUI

Generates images and video on local hardware.

ElevenLabs (https://elevenlabs.io)

Creates narration, transcripts and sound effects.

HyperFrames

Edits media, animates scenes and renders video.

Review

You watch the result and request changes.

You guide the work. AI coordinates the tools.

_Image: The tools behind the workflow_

**Speaker notes:**

You supply a brief and media to the AI coordinator. The AI sorts references, writes a storyboard and calls the tools. ComfyUI generates visual assets when needed. ElevenLabs supplies narration, transcription and sound effects. HyperFrames assembles the media and animations into a video. You review and direct revisions. ComfyUI generation can run locally; ElevenLabs is a hosted service. HyperFrames: https://github.com/heygen-com/hyperframes ; ComfyUI: https://github.com/Comfy-Org/ComfyUI ; ElevenLabs: https://elevenlabs.io/docs/overview

## Slide 6: The video spec

1. Gather the media

Drop footage, photos, audio and references
into one shared project folder.

2. Audit and categorize

Ask AI to tag assets by content and quality,
flag duplicates and identify missing shots.

3. Provide the project brief

Define the audience, purpose, key message,
style, target length and delivery format.

4. Build the storyboard

Map the story into scenes. Assign media,
narration and timing to each beat.

5. Build and review clip by clip

Generate or edit one clip, review and refine,
then move on. Assemble and review the cut.

The spec for each clip

Exact assets, narration, duration, on-screen text,
typography, motion, transitions, music and sound.

_Image: The work starts with a plan_

**Speaker notes:**

1. Give the AI all useful media and references: photos, videos, music, documents, and even website source code. Ask it to categorize, label and assess them. 2. State the audience, purpose, main message, length and destination. 3. Build the storyboard together. Agree on music, beats, exact media per beat, narration, typography, animations and any generated shots. 4. Finish a clip-by-clip video spec before handing over assembly. This is the most important planning work. 5. Choose review one scene at a time, or a complete first cut. Planning improves alignment but does not remove the need to check image selection, timing and accuracy.

## Slide 7: HyperFrames

HTML, CSS and media
become a video.

An open-source editing and animation
framework that an AI can control.

The AI writes the animation.
HyperFrames previews and renders it.

github.com/heygen-com/hyperframes (https://github.com/heygen-com/hyperframes)

Setup

Install Node.js 22+ and FFmpeg
Run: npx hyperframes skills update
Ask your AI to create a project

_Image: Wordle animation from Lenovo Quality Month_

**Speaker notes:**

HyperFrames is an open-source framework for rendering HTML, CSS, media and seekable animation into MP4. It gives an AI a programmatic editing workspace with a timeline, preview and renderer. It can implement custom animation within browser and rendering capabilities. Setup: install Node.js 22+ and FFmpeg, use npx hyperframes skills update to install the core agent skills, then ask the AI to create a project. For manual scaffolding: npx hyperframes init my-video. Repository and setup: https://github.com/heygen-com/hyperframes . Catalog: https://hyperframes.heygen.com/catalog . The local project uses its pinned wrapper for reproducibility.

## Slide 8: HyperFrames component catalog

Ready-made animations for your videos

The catalog includes charts, carousels,
text effects and transitions you can reuse.

1. Browse the catalog and pick a component.

2. Give AI its link or install command.

3. Ask AI to adapt it to your media,
branding and timing.

hyperframes.heygen.com/catalog (https://hyperframes.heygen.com/catalog)

Example: npx hyperframes add bar-chart-race

_Image: Reusable animations_

**Speaker notes:**

The catalog contains reusable components. Pick a component and give its link or install command to the AI. This animated walkthrough reconstructs that action in HyperFrames; it is not a recording of a live terminal. Example: npx hyperframes add bar-chart-race. The AI can change colors, typography, data, timing and layouts, or build a custom component. Catalog: https://hyperframes.heygen.com/catalog . Other requested examples: flowchart, hw-pipeline and chatgpt-exchange.

## Slide 9: Components in a real video

Leaderboard

Section carousel

Tile reveal

Employee faces and growing point totals

Your photos, section names and timing

Your images and final title

Ask AI to change the colors, content or motion. Export as video or GIF.

_Image: leaderboard_

_Image: carousel_

_Image: tile-flip_

**Speaker notes:**

These are extracts from the Lenovo Quality Month video. The examples show a points leaderboard, a section carousel, and the closing tile reveal. Reusing a component gets the first version on screen quickly; data, timing and media still need direction and review. The animations remain editable in their HyperFrames source compositions.

## Slide 10: ElevenLabs setup

1. Create an API key

Create a key in the ElevenLabs dashboard.
Save it in your project’s .env file.

2. Choose a voice for text-to-speech

Listen to samples in the Voice Library.
Choose a voice and copy its voice ID.

3. Give your workflow the voice ID

Set ELEVENLABS_VOICE_ID in .env, or give
the voice ID directly to your LLM in the chat.

Then ask AI to generate a short TTS sample.

Quality Month narration example

Your project’s .env

ELEVENLABS_API_KEY=your_api_key
ELEVENLABS_VOICE_ID=your_voice_id

_Image: Narration example_

**Speaker notes:**

Create an ElevenLabs API key, then store it in the local project .env file as ELEVENLABS_API_KEY. Browse and audition voices in the Voice Library, add the chosen voice if needed, and copy its voice ID. This workspace reads ELEVENLABS_VOICE_ID as its default or accepts a voice ID per request. The voice ID may also be given directly to the LLM so it passes that ID when generating speech. Ask the AI to load .env and configure the TTS integration, then generate a short sample before the full narration. The values shown are examples, not credentials. API setup: https://elevenlabs.io/docs/eleven-api/quickstart . Voices: https://elevenlabs.io/app/voice-library . Local environment variable support: README.md and scripts/services.mjs. The embedded sample uses voice UgBBYS2sOqTuMpoF3BR0.

## Slide 11: ComfyUI

Lenovo Nerve shot generated with MiniMax H3

Local generation with downloaded models

The workflow runs on your own hardware.

I used AI to rewrite
my video prompts

1. Share the H3 prompting guide with AI.

2. Describe the shot and provide the
first and last frames.

3. Ask AI to rewrite the prompt
using the guide, then queue it
in ComfyUI.

MiniMax H3 prompt guide (https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/docs/VIDEO_PROMPT_WRITING_GUIDE_base_en.md)

Click to open the guide on Hugging Face

_Image: MiniMax H3 generated shot_

**Speaker notes:**

I used AI to rewrite my video prompts with the MiniMax H3 prompting guide. I gave it the shot description and reference frames, then asked it to structure the camera movement, timing and sound for the model. The guide is linked on this slide. ComfyUI is an open-source node-based interface and backend. Local workflows run downloaded model files on your own hardware. Model size and workflow determine memory and compute requirements. This machine has an RTX 5090 with 32 GB VRAM. ComfyUI can also use hosted API nodes; those are not local inference. The example is a local MiniMax H3 shot from the Nerve project. Modes include text-to-video, first-frame-to-video, first-and-last-frame-to-video and last-frame-to-video. Sources: https://github.com/Comfy-Org/ComfyUI ; https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/docs/VIDEO_PROMPT_WRITING_GUIDE_base_en.md ; Lenovo-Nerve-Handoff/HyperFrames-Project/assets/generated/screw-boss-v1/workflow-underside-zoom.json and zoom-generation-job.json.

## Slide 12: Keeping generated clips connected

_Image: Keeping generated clips connected_

**Speaker notes:**

Use the last selected frame of clip A as the first frame of clip B. Choose an end frame for clip B, then describe the movement between the two. In the actual Nerve workflow, workflow-pillar-dive.json loads zoom-trimmed-last-frame.png and end-pillar-macro-sharp-v2.png. This shares an image at the boundary to help visual continuity, but motion and identity still need review. First and last frames can come from a ComfyUI image workflow or an image-capable LLM. For this POC we keep media within the agreed existing-project, ComfyUI and HyperFrames sources. Strongly recommended: provide the H3 prompt-writing guide to your AI so it follows the model's prompting structure. Ask it to validate installed nodes and models before queueing, then save the workflow, prompt, seed and outputs. Guide: https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/docs/VIDEO_PROMPT_WRITING_GUIDE_base_en.md

## Slide 13: Resources

HyperFrames catalog (https://hyperframes.heygen.com/catalog)

hyperframes.heygen.com/catalog (https://hyperframes.heygen.com/catalog)

MiniMax H3 prompt guide (https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/docs/VIDEO_PROMPT_WRITING_GUIDE_base_en.md)

Hugging Face · MiniMaxAI / MiniMax-H3

ComfyUI installation (https://docs.comfy.org/installation/desktop/windows)

docs.comfy.org/installation/desktop/windows

ElevenLabs (https://elevenlabs.io)

elevenlabs.io

**Speaker notes:**

Resources: HyperFrames catalog https://hyperframes.heygen.com/catalog ; MiniMax H3 prompt guide https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/docs/VIDEO_PROMPT_WRITING_GUIDE_base_en.md ; ComfyUI Windows installation https://docs.comfy.org/installation/desktop/windows ; ElevenLabs https://elevenlabs.io . Theme adapted from the supplied Cake 2.0 LXG presentation template, with no LXG logo.
