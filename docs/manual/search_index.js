// written by build_offline.py -- the manual's sections, for search.js
window.MANUAL_INDEX = [
{
"part": "StereoCrafter Studio",
"title": "Contents",
"href": "index.html",
"text": ""
},
{
"part": "StereoCrafter Studio · Contents",
"title": "1 · Getting started",
"href": "index.html",
"text": "What Studio does Installing and starting Studio A tour of the window Your project folder Beginner Mode"
},
{
"part": "StereoCrafter Studio · Contents",
"title": "2 · The pipeline",
"href": "index.html",
"text": "Scene Cut DepthCrafter Splatting Inpainting Merging"
},
{
"part": "StereoCrafter Studio · Contents",
"title": "3 · Depth Map Editor",
"href": "index.html",
"text": "When to fix a depth map Setting up SAM 3 Masking objects Changing depth inside a mask Rendering and edit history"
},
{
"part": "StereoCrafter Studio · Contents",
"title": "4 · Running jobs",
"href": "index.html",
"text": "The job queue and progress Stopping safely GPU memory Unattended runs"
},
{
"part": "StereoCrafter Studio · Contents",
"title": "5 · Customising",
"href": "index.html",
"text": "Settings and themes Saving and loading settings"
},
{
"part": "StereoCrafter Studio · Contents",
"title": "Appendices",
"href": "index.html",
"text": "Recommended settings by graphics card File names Troubleshooting and log files Preview views Glossary Credits"
},
{
"part": "Getting started",
"title": "1 What Studio does",
"href": "part1.html#what",
"text": "StereoCrafter Studio turns an ordinary 2D video into 3D. It does this in steps, and each step has its own tab at the top of the window. You work through them from left to right, and the result of each step is what the next one starts from. EXTRA Scene Cut Splits a whole movie into short clips, one per shot, and sorts them into chapters. The cutting is a must; using this tool for it is optional. 1. Depth Map Makes a depth map for each clip, with DepthCrafter or VDA (Video Depth Anything): a black-and-white video where white is near and black is far. 2. Splatting Uses the depth map to build the second eye's view: things shift sideways by how near they are. This is where your main 3D effect editing happens: Disparity (Depth Strength), Depth Map Pre-processing (Edge Alignment) and the Special FX. 3. Inpainting The Splatting stage creates holes in the frame where the image was warped. Inpainting uses generative AI to fill in those gaps for a much cleaner right eye view. 4. Merging Puts the left and right eyes together into the finished 3D video. Beside the four numbered stages, the Extra tab holds Scene Cut, the Depth Map Editor, where you can fix a depth map by hand before Splatting uses it (Part 3), and Edge Fill, for the strip a deep warp leaves at a frame's edge."
},
{
"part": "Getting started · 1 What Studio does",
"title": "Where your clips come from",
"href": "part1.html#what",
"text": "DepthCrafter needs your movie cut into short clips, one per shot. It's a temporal model: it looks at a run of frames together and keeps the depth steady from one frame to the next, as long as they belong to the same scene. Give it a clip with several shots in it and it tries to keep the depth consistent across the cuts too, between scenes that have nothing in common, and the depth map quickly gets worse. So every clip should be a single shot. What's optional is the tool you cut with. You can use any you like: DaVinci Resolve's scene cut detection, another editor, or by hand. Put the clips in your Source folder (section 4) and start at DepthCrafter. Scene Cut is included for convenience, so you can do the cutting without leaving Studio, which is why it sits in the Extra tab rather than being a numbered stage."
},
{
"part": "Getting started · 1 What Studio does",
"title": "Where the 3D look is made",
"href": "part1.html#what",
"text": "Most of your creative choices happen in Splatting. Disparity (Depth Strength) sets how deep the 3D is, Depth Map Pre-processing (Edge Alignment) tidies the depth map so its edges line up with the edges of objects in the picture (no shimmering or ragged outlines), and the Special FX add things like Pop Out Boost, Frame Break and Rain (rain put at depths of its own). The other stages mostly run on their own once they're set up. Two namesA name in brackets is the plainer one Beginner Mode shows in its place: Disparity is Depth Strength, the Depth Map Pre-processing panel is Edge Alignment, and its Dilate knobs are Expand. This manual uses the normal names first."
},
{
"part": "Getting started · 1 What Studio does",
"title": "What you end up with",
"href": "part1.html#what",
"text": "A side-by-side 3D video: the left eye's picture and the right eye's picture next to each other in one frame. 3D TVs, VR headsets and 3D players know how to show each picture to the right eye. Good to knowEvery step runs on your graphics card, and the AI steps (DepthCrafter and Inpainting) take a while: minutes per clip, not seconds. Studio is built to run a whole chapter on its own while you do something else."
},
{
"part": "Getting started",
"title": "2 Installing and starting Studio",
"href": "part1.html#install",
"text": ""
},
{
"part": "Getting started · 2 Installing and starting Studio",
"title": "What your computer needs",
"href": "part1.html#install",
"text": "Windows and an NVIDIA RTX graphics card: any RTX 20, 30, 40 or 50 series card, or an RTX workstation card, with an up-to-date driver (version 580 or newer). Studio does its work on the card. GTX 10 series and older NVIDIA cards can't run it, and neither can cards from other makers. Graphics memory (VRAM): the more the better. 16 GB runs everything comfortably; smaller cards work with lighter settings (see Appendix A). Inpainting V2, an experimental option for problem scenes only, is the exception: it wants 20 GB or more, or plenty of system memory instead (see Inpainting V2). Studio has been tested on a machine with an RTX 5060 Ti with 16 GB of VRAM and 32 GB of system RAM. Two free tools that must be installed and on your PATH (so any program can find them): Git (the installer installs it if it's missing) and FFmpeg -- a full build, with ffprobe. You don't need the CUDA Toolkit: Studio's PyTorch brings its own CUDA. Studio checks the driver and FFmpeg each time it starts and tells you what's missing. Plenty of disk space. Every step writes full videos, and a long chapter can use tens of gigabytes while it runs."
},
{
"part": "Getting started · 2 Installing and starting Studio",
"title": "Installing",
"href": "part1.html#install",
"text": "Make a folder where you want Studio to live, and run the installer script StereoCrafter_Studio_Installer.bat from inside it. Start Studio (below) and get the models in Settings → Studio → Models: it lists each one, whether it's on this PC, and a Download for each one missing -- only the files Studio loads (Stable Video Diffusion, for one, is 1.5 GB there rather than 18). For the main pipeline that's DepthCrafter, Stable Video Diffusion and StereoCrafter, about 7.5 GB. Stable Video Diffusion is gated: accept its terms on its Hugging Face page and sign in there with a read token first. Each model is needed only for the tools that use it: DepthCrafter and Stable Video Diffusion (gated) for DepthCrafter; StereoCrafter and Stable Video Diffusion for Inpainting. SAM 3 (Depth Map Editor, 3.4 GB, gated): also Download Weights on the editor's Segment page -- see Setting up SAM 3. ROSE (Erase from Clip, Edge Fill, about 3.7 GB): Set Up ROSE in the editor's Erase panel -- it builds an environment of its own. Scene Cut: OmniShotCut, and DINOv3 (gated) and SSCD for cut validation, download in Models; TransNetV2 comes with Studio. AutoShot is only in the SceneCutGUI bundle on Google Drive (Get It opens it): put ckpt_0_200_0.pth in the models folder's SceneCut folder. Inpainting V2 (experimental, 46.6 GB): also Download V2 Weights on its page -- Wan 2.1 VACE's text encoder, VAE and tokenizer, and StereoCrafter2. TipA stage whose model isn't there says so when you press Start, and which one to get in Models."
},
{
"part": "Getting started · 2 Installing and starting Studio",
"title": "Starting Studio",
"href": "part1.html#install",
"text": "Double-click StereoCrafter Studio.exe in your Studio folder. A small black window may flash up for a moment while Studio checks that everything is installed; then the Studio window opens on the stage you used last. Your project folder sits in the middle of the title bar, at the top of the window. Until you choose one it reads Select project folder…: click it and pick the folder your project lives in. From then on every stage keeps its input and output for each chapter inside it, and the project's name shows there instead. Once you have a few projects in one folder, the same click lists them all as tiles to switch between (Switching projects; section 4 shows how to lay a project out). The project button in the middle of the title bar, here on Demo Project, with the list it opens: each project a tile, its poster a picture from the project's folder. Open Other Project… picks any folder. A desktop shortcutRight-click StereoCrafter Studio.exe and choose Send to > Desktop (create shortcut) (on Windows 11, Show more options first) to start Studio from your desktop, or pin it to the taskbar. The App Icon picker in the theme popout changes the icon's colour live -- on the window, the taskbar and that shortcut. Studio remembers your settings, folders, theme and window size by itself, and saves them when you close it. If something goes wrong before the window appears, a window opens with the error so you can see what happened."
},
{
"part": "Getting started",
"title": "3 A tour of the window",
"href": "part1.html#tour",
"text": "Every stage's page is laid out the same way, so once you know one you know them all. This is DepthCrafter's page. 1 2 3 4 5 6 7 8 9 10 The DepthCrafter page, with a chapter picked. Title bar. Drag it to move the window, double-click it to fill the screen. The three buttons on the right are the usual minimise, maximise and close. In the middle is the name of the project you're working on: click it to switch to another project (see Switching projects). Stage tabs. One tab per step, numbered in the order you use them, then Extra. Click a tab to change stage. Ctrl+Tab goes to the next one. Hover over Extra to pick one of its tools from the list that drops down; a click on the tab opens the one you used last. Theme. Colours and look: pick a theme, and turn Frosted Glass, Section Outlines and Film Grain on or off. The UI Scale knob makes everything bigger or smaller (Part 5). Beginner Mode. Hides the advanced settings and explains everything in plainer words. See section 5. Settings (the gear, or Ctrl+,). Everything that isn't on the page: your project folder, saving settings, the GPU, and each stage's extra options. Right of it, the bell keeps every message Studio has shown this session (a job finished, a folder set, a problem), newest first; a number on it counts the ones you haven't seen. Click it to read them, and Clear All empties it. VRAM. How much of your graphics card's memory is in use right now, by every program, not only Studio. Click it for the details. The page. The stage's settings, in panels. Hover over any setting, or its name, to see what it does. Current Clip Info shows the clip being worked on, and has the Open Console button (more on the console below). The action bar. The progress of the running job, a line saying what it's doing, and the buttons that start and stop it. View Output Folder opens the folder the results go to. Status line. Ready when nothing is running; otherwise which stage is busy and what it's doing, whichever tab you're looking at."
},
{
"part": "Getting started · 3 A tour of the window",
"title": "Settings",
"href": "part1.html#tour",
"text": "The gear opens Settings. The list on the left starts with the options of the stage you're looking at, so it changes as you change tabs, then Studio, Appearance, Keyboard Shortcuts, Tips and GPU. Tips lists every tip in Studio, by stage -- the same ones each stage shows, one at a time, just above its progress bar. Settings → Studio: save everything now, set your project folder, check for a newer Studio, open the console, or quit. Appearance has two groups: Window: Show Tips turns the tips above each stage's progress bar on or off, and Fit Window to Content sizes the window to the page you're on (Ctrl+0 does the same from anywhere). Projects: the Projects Folder that holds all your projects, and an optional TV Shows Folder if you keep shows somewhere else. See Switching projects. At the top of Studio, Open the Manual opens this manual, and at the top of each stage's section a button opens that stage's own part of it (F1 does the same from any page). Clicking StereoCrafter Studio in the window's top-left corner opens it at its contents too. Check for Updates looks for a newer Studio on its GitHub page and can install it -- there's nothing to set up. Check for Updates at Startup (off until you turn it on) does that check once, a few seconds after Studio opens, and tells you when there's something new; it doesn't check again that session unless you click Check for Updates, and it never installs anything by itself. A copy of Studio downloaded as a ZIP can update too. The first check says so and lists the files that differ from the latest version; Install & Restart brings it up to date and sets it up so later updates work as usual. Your settings, models, projects and outputs stay; changes made to Studio's own files are replaced. Models shows where Studio keeps its models and which of them are on this PC: Models folder: Change… points Studio at another folder or drive -- one with more room, or one two installs share. Nothing is moved or copied: move the model folders there yourself, or download them there. A model not moved yet is still found in Studio's own weights folder, and Use Default goes back to that one. Not while a job runs. Hugging Face: Stable Video Diffusion, SAM 3 and DINOv3 are gated. Open the model's page (Ask for Access on its row, or the ⓘ beside it) and ask for access -- Stable Video Diffusion's is granted at once, the others by their makers. Then make a Read token in your Hugging Face settings (Get a Token), paste it in and press Sign In. It's kept where hf auth login keeps it, so the hf command and Studio share it; Studio never saves it in its own settings. Download on a model's row gets just the files Studio loads, into the models folder. One downloads at a time and the others queue behind it; while a job runs, a download waits for it to finish, so the job isn't slowed. Stop keeps what came down: the next Download carries on from there. Already have StereoCrafter?Point Change… at its weights folder and Studio uses those models where they are -- nothing to download again. Studio looks for the same folder names (stable-video-diffusion-img2vid-xt-1-1, DepthCrafter, StereoCrafter, and SceneCut from the SceneCutGUI bundle), so those rows show Installed. Pick the weights folder itself, not the install above it. A model it doesn't have downloads into that folder, and ROSE needs Set Up ROSE again."
},
{
"part": "Getting started · 3 A tour of the window",
"title": "Picking folders and clips",
"href": "part1.html#models",
"text": "Every Browse button opens Studio's folder picker. Each level of folders sits side by side, every folder shows how many clips (or folders) it holds, and the last column lists the clips in the folder that's open. Click a folder to open it in the next column; double-click it, or press Select Folder, to use it. Where a single clip will do (the input folders of DepthCrafter, Splatting, Inpainting and Merging), click a clip and press Select Clip to work on just that one; double-clicking it picks it straight away. Output folders take a folder only. Right-click in a column for New Folder: type its name and press Enter (Esc cancels). Right-click a folder or clip for Move to Recycle Bin. Studio asks first, and you can put it back from the Recycle Bin. On a drive with no Recycle Bin (a network share, some USB drives) nothing is deleted. Something too big for its drive's Recycle Bin can't go there: Studio says so and asks, as Windows does -- Delete Permanently removes it for good, Keep It leaves it. The open project's folder (and a folder holding it) and the folders a running job is using can't be moved to the Recycle Bin. The path along the top is clickable, and the buttons at the top right jump to your project folder or any drive. Under the drives are four Shortcuts for the folders you use most. Click an empty one (+ Shortcut) to keep the highlighted folder there; click it later to jump straight back. Right-click a shortcut to change or remove it. Windows Explorer… opens Windows' own dialog instead, if you'd rather pick that way. Where a clip will do too, it asks whether you're picking a folder or a clip."
},
{
"part": "Getting started · 3 A tour of the window",
"title": "The console",
"href": "part1.html#models",
"text": "The console is a separate window that shows what Studio is doing behind the scenes, line by line. You don't need it to use Studio, but it's the first place to look when something goes wrong. Open it with Open Console, or Ctrl+`. One tab per stage, plus All, which has everything. Progress bars are drawn as bars. Each stage's tab shows only that stage's messages; All shows every message. DepthCrafter's, Splatting's and Inpainting's tabs keep to the essentials: each clip's separator line, the progress bars, the total times, and any warnings or errors. The rest of their detail is on All (Splatting's and Inpainting's debug options open their tabs up again). Every clip starts with a separator line giving its number in the batch and its name, so it's easy to see where one clip's messages end and the next one's begin. The project's name is shown at the top of the console too. Drag across lines to select them, and Ctrl+C copies what you picked: handy for asking for help. The copy button copies the whole tab. Follow keeps the newest line in view. It switches off when you scroll up to read. Closing the console only hides it. Messages keep arriving, and they're all there when you open it again."
},
{
"part": "Getting started · 3 A tour of the window",
"title": "Keyboard shortcuts",
"href": "part1.html#models",
"text": "Every shortcut is also listed in Settings → Keyboard Shortcuts. Single keys and arrows work when you're not typing in a box. Ctrl+1 … Ctrl+4Go to Depth Map, Splatting, Inpainting or Merging (Ctrl+5: Extra) Ctrl+TabNext stage (Ctrl+Shift+Tab: the one before) Ctrl+POpen the project list (Esc closes it) Ctrl+,Open or close Settings Ctrl+`Show or hide the console Ctrl+0Fit the window to the page you're on Ctrl+= · Ctrl+−UI Scale up · down F1Open this manual at the stage you're on (in Extra: Scene Cut, or the Depth Map Editor's part) ← →In a preview: one frame back · forward (Shift: ten) Home · EndIn a preview: the first · last frame ↑ · ↓In a preview: the next · previous clip SpaceIn a preview: play or pause (Shift+Space: fast forward) F11 · EscFull Preview, full screen (Splatting, Merging) · close it (F11 too) Ctrl+SSplatting: create or update the clip's sidecar Ctrl+TSplatting: Detect Text Ctrl+MSplatting: Edit Depth Map (the clip in the Depth Map Editor) S · DSplatting: add a static · dynamic keyframe S · DDepth Map Editor: key the selected layer, static · dynamic Ctrl+Z · Ctrl+YDepth Map Editor: undo · redo the last edit (Ctrl+Shift+Z redoes too) I · ODetect Text: Set From · Set To at the frame showing Ctrl+C · Ctrl+AIn the console: copy what's selected · select the whole tab"
},
{
"part": "Getting started",
"title": "4 Your project folder",
"href": "part1.html#project",
"text": "You can point each stage at any folders you like, but it's much easier to keep each movie in one project folder laid out the way Studio expects. Then you pick a chapter from a list and Studio fills in every folder for every stage by itself."
},
{
"part": "Getting started · 4 Your project folder",
"title": "How to lay out the folders",
"href": "part1.html#project",
"text": "One project folder per movie. Inside it, one folder per stage; inside each of those, one folder per chapter: Demo Moviethe project folder: choose this one in Studio DM Source Clips Scene Cut puts the clips here Chapter 1 - Openingthe clips Chapter 2 - The Chasethe clips Chapter 3 - Rooftopsthe clips → DM DepthMaps DepthCrafter's depth maps Chapter 1 - Openingdepth maps Chapter 2 - The Chasedepth maps Chapter 3 - Rooftopsdepth maps → DM Splats Splatting's results Chapter 1 - Openinghires/ · lowres/ Chapter 2 - Chasehires/ · lowres/ Chapter 3 - Rooftopshires/ · lowres/ → DM Inpainted Inpainting's results Chapter 1 - Openingfilled-in clips Chapter 2 - The Chasefilled-in clips Chapter 3 - Rooftopsfilled-in clips → DM Merged the finished 3D (Merging) Chapter 1 - Opening3D videos Chapter 2 - The Chase3D videos Chapter 3 - Rooftops3D videos The outlined folders are the same chapter in every stage. Studio matches them by their number, so \"The Chase\" in one and \"Chase\" in another is fine. You make the chapter folders; each stage fills its own. Studio finds the folders by a few simple rules, so you can name them your own way: Stage folders are found by a word in their name. The name just has to contain Source, Depth, Splat, Inpaint and Merged. For the last one, a name with LR as a word of its own works too (such as DM Converted LR Clips), if you already use that. Anything else in the name is up to you: here every folder starts with the movie's initials, DM, so they're easy to tell apart from another movie's. Chapter folders are matched by their number, not their whole name. Start each chapter folder's name with Chapter and its number (Ch 2 and C2 work too). If the name after the number differs a little between stages (\"The Chase\" in one, \"Chase\" in another), Studio still knows they're the same chapter. Folders without a chapter number are ignored, so a spare folder of notes or tests won't confuse it. You only make the chapter folders. Each stage creates what it needs inside them, such as Splatting's hires and lowres."
},
{
"part": "Getting started · 4 Your project folder",
"title": "TV shows",
"href": "part1.html#project",
"text": "A show has one more layer or two: seasons and episodes. Inside each episode's folder, lay out the stage folders exactly as for a movie: Demo Showthe project folder: choose this one in Studio Season 1 Episode 1 1. S1E1 Source Clips→2. S1E1 DepthMaps→3. S1E1 Splats→4. S1E1 Inpainted→5. S1E1 Merged inside each: Chapter 1 Chapter 2 … Episode 2 1. S1E2 Source Clips→2. S1E2 DepthMaps→3. S1E2 Splats→4. S1E2 Inpainted→5. S1E2 Merged inside each: Chapter 1 Chapter 2 … Each episode is laid out like a movie. Number the stage folders (1. to 5.) if you like them in pipeline order; Studio goes by the word in each name. The chapter list then names each chapter with its episode, such as S1E1 · Chapter 1. Studio counts a project as a TV show when it has a folder with Episode in its name somewhere inside. A movie kept one folder down (in a Movie folder, say) is still a movie."
},
{
"part": "Getting started · 4 Your project folder",
"title": "The finished folders",
"href": "part1.html#project",
"text": "When a stage has used a clip, it moves that clip into a folder called finished inside the folder it came from. That way, if you start the same chapter again, it carries on with the clips it hasn't done yet instead of starting over. The next stage knows to look in finished, so you never have to move anything yourself. Good to knowA clip that has been through several stages can end up a few finished folders deep. That's normal, and Studio finds it."
},
{
"part": "Getting started · 4 Your project folder",
"title": "Telling Studio where the project is",
"href": "part1.html#project",
"text": "Open Settings (the gear) → Studio, and next to Set Project Folder press Open. Choose the project folder itself: Demo Movie in the example, not one of the folders inside it. The project's name then shows in the middle of the title bar."
},
{
"part": "Getting started · 4 Your project folder",
"title": "Switching projects",
"href": "part1.html#switch-project",
"text": "If you keep all your projects in one folder, tell Studio where it is once: Settings → Appearance → Projects Folder. Then click the project name in the title bar, and every project in that folder is shown as a tile. Click one to switch to it. Movies and TV Shows are on separate tabs. The list opens on the tab of the project you're in. Posters. Put a picture (.jpg or .png) in a project's folder and it becomes that project's tile. Without one, Studio uses the cover art stored inside the movie's video file, if it has some (it ignores a plain frame from the film saved as a thumbnail). Otherwise the tile shows a folder. Your projects folder can be laid out three ways: projects straight inside it, movies and shows mixed; or Movies and TV Shows folders inside it, each holding projects; or shows kept somewhere else entirely, with that folder set as the TV Shows Folder in Settings. Open Other Project… picks any folder, even one outside your projects folder. Change Projects Folder… points Studio at a different one. Each project remembers where you were. When you switch, every stage goes back to the chapter (or the folders) it last used in that project. Come back later, and DepthCrafter is still on chapter 5 while Splatting is on chapter 3."
},
{
"part": "Getting started · 4 Your project folder",
"title": "Picking a chapter",
"href": "part1.html#switch-project",
"text": "Every stage's folder panel now has a chapter list at the top right. Pick a chapter, and the stage's input and output folders are filled in from your project. The folder fields fold away, since you don't need to see them. 1 2 1 The chapter list. 2 Change Folders shows the folders a chapter filled in, if you want to check or change them. Each stage has its own chapter choice, because you'll usually have different chapters at different stages: DepthCrafter can work on chapter 5 while Splatting is still on chapter 3. Choose Manual paths at the top of the list to set the folders yourself again."
},
{
"part": "Getting started",
"title": "5 Beginner Mode",
"href": "part1.html#beginner",
"text": "Studio has a lot of settings, and most of them are for fine-tuning. Beginner Mode, the switch at the top right, keeps the page down to the settings you need to get a good result: Advanced settings are hidden. They keep their values; they're just out of the way. Splatting's Rain is hidden too, since it needs tuning for each scene. Hover help is in plain language, like this manual. A few settings get plainer names. A few options are set for you to the safe choice, such as making both the full-size and the smaller Splatting results, which the later stages need. Never use it? Settings > Studio > Hide Beginner Mode Switch takes the switch off the top bar. It comes back on its own while Beginner Mode is on, so you can always turn it off. Splatting with Beginner Mode on. Compare with the same page with it off. Turn it off whenever you want everything back. All your settings, and the options it set for you, go back to how you had them. Beginner Mode can't be switched while a job is running. TipNew to 3D conversion? Leave Beginner Mode on for your first few chapters. The defaults are chosen to give a good result, and you can learn the rest one setting at a time."
},
{
"part": "The pipeline",
"title": "6 Scene Cut",
"href": "part2.html#scenecut",
"text": "Scene Cut chops a whole movie into short clips, one for every shot, ready for DepthCrafter. You'll find it in the Extra tab, under Scene Cut. Required: one clip per shotYour movie must be cut into separate shots before DepthCrafter. What's optional is the tool: Scene Cut, or any other that cuts a video into one clip per shot, such as DaVinci Resolve's scene cut detection. Scene Cut is included so you can do it without leaving Studio."
},
{
"part": "The pipeline · 6 Scene Cut",
"title": "Why every shot needs its own clip",
"href": "part2.html#scenecut",
"text": "DepthCrafter is a temporal model: it works on a run of frames at once and keeps the depth of the same scene consistent from frame to frame, so things don't flicker nearer and further. That's exactly what goes wrong across a cut. With several shots in one clip, it tries to keep unrelated scenes consistent with each other, and the depth map quickly loses quality. The later stages benefit too: Inpainting also works through a clip in pieces, and a fill that suddenly changes scene halfway through looks wrong."
},
{
"part": "The pipeline · 6 Scene Cut",
"title": "Before you start: the model files",
"href": "part2.html#scenecut",
"text": "Scene Cut finds the cuts with an AI. Each detector needs its own model file in the weights/SceneCut folder inside your Studio folder: ckpt_0_200_0.pthAutoShot transnetv2-pytorch-weights.pthTransNetV2 OmniShotCut_ckpt.pthOmniShotCut DINOv3/ (the whole folder)Validate cuts with DINOv3 (optional) You only need the ones you'll use. If a file is missing, Scene Cut tells you which one rather than failing. 1 2 3 4 5 Scene Cut, set up to cut a movie into chapters of clips."
},
{
"part": "The pipeline · 6 Scene Cut",
"title": "1 Input & Output",
"href": "part2.html#scenecut",
"text": "Video File: the movie to cut. Use Browse…, or drop the file onto the field. Output Folder: where the clips go. With a project folder set (Part 1, section 4) it's filled in with your Source folder, and Split into chapters is turned on. Scene Cut always takes the whole movie, so it has no chapter list. Change the folder and Studio keeps yours for that project. Split into chapters (on from the start) files the clips into one folder per chapter, the way your project is laid out. It uses the chapter markers inside the movie file, which a Blu-ray or DVD rip usually has. An existing Chapter N folder is reused; a missing one is made and named from the marker. A shot that runs across a chapter start is cut there."
},
{
"part": "The pipeline · 6 Scene Cut",
"title": "2 Detection Configuration",
"href": "part2.html#scenecut",
"text": "Detector picks the AI that finds the cuts: AutoShot: a good all-rounder. Try it first. TransNetV2: a second opinion for the same kind of footage. OmniShotCut: better for cartoons, anime, games and screen recordings. It also understands fades and dissolves. The settings beside it change with the detector. For AutoShot and TransNetV2: device: leave on auto, which uses your graphics card. threshold: how sure the AI must be to call something a cut. Lower finds more cuts, including some that aren't real; higher finds fewer. min_scene_len: the shortest a clip may be, in frames. Anything shorter joins the clip next to it, so a camera flash doesn't become a clip of its own. OmniShotCut has its own: clean_shot mode keeps only hard cuts and folds fades into the shot before them (start with this); a confidence above 0 drops the doubtful cuts (raise it in small steps, such as 0.3). FFmpeg Output sets how the clips are saved. They're always high-quality 10-bit video, made on your graphics card. Preset trades speed for quality (p7 is the best and the default) and CQ Level sets the quality (lower is better and bigger; 16 is already high)."
},
{
"part": "The pipeline · 6 Scene Cut",
"title": "3 Refinements & Validation",
"href": "part2.html#scenecut",
"text": "Snap cuts to frame-perfect boundary: leave it on. It nudges each cut onto the exact frame, so no clip starts with a frame of the shot before. Validate cuts with DINOv3 AI: a second, slower AI double-checks every cut and throws away false alarms from camera flashes, fast movement and near-black frames. Turn it on if you're getting cuts where nothing changed. Validation Window and Flash Sensitivity fine-tune it; the defaults are fine for most films."
},
{
"part": "The pipeline · 6 Scene Cut",
"title": "4 Output Actions",
"href": "part2.html#scenecut",
"text": "Split video into scenes: the one that matters, on from the start. It writes each shot out as its own clip, which is what DepthCrafter works on. Auto Crop (with Split video into scenes; on from the start): crops each shot to its own picture, for movies whose shape switches -- an IMAX sequence at 1.90:1 in a 2.39:1 film, a 4:3 flashback in a 16:9 show. It reads the black bars across the whole movie first, finds the few bar sizes it really uses, and gives each shot the one that fits it, so a dark shot is never cropped into. The crop happens on the graphics card as each shot is decoded, so it costs next to no time. The later stages handle the mixed shapes: turn on Auto Size for the Loaded Clip in DepthCrafter and Splatting so each clip runs at its own shape's size, and each clip's shape shows in Upcoming Jobs. Export scene list to CSV / HTML: a list of every shot with its start and end times, as a spreadsheet or a web page. Export to .sc file: the cuts as a list DaVinci Resolve can read, so they appear on its timeline. SC Offset shifts them a few frames if your timeline counts from somewhere else. Save scene thumbnails: a few stills from each shot, so you can see what's in it without opening it. Images is how many, Margin how many frames in from each end they're taken."
},
{
"part": "The pipeline · 6 Scene Cut",
"title": "5 Run Process",
"href": "part2.html#scenecut",
"text": "Press Start Processing. The bar shows how far along it is; Abort stops it. A whole movie takes a while, mostly for writing the clips. Open Console, at the right of the Run Process heading, shows the run as it goes, with its progress bar, on the console's Extra tab. When it's done, go to 1. Depth Map and press Import from Scene Cut: its input is filled in with the new clips."
},
{
"part": "The pipeline · 6 Scene Cut",
"title": "Using another tool instead",
"href": "part2.html#scenecut",
"text": "If you'd rather cut the movie elsewhere, a few things keep the clips at their best: One clip per shot, cut on the exact frame. Keep the quality high. Every later stage builds on these clips, so export at high quality, ideally 10-bit. Save them into the chapter's Source folder, such as DM Source Clips/Chapter 1 - Opening."
},
{
"part": "The pipeline · 6 Scene Cut",
"title": "When the cuts aren't right",
"href": "part2.html#scenecut",
"text": "Cuts are missedLower the threshold a little, or try another detector. On animation or games, try OmniShotCut. Cuts where nothing changedRaise the threshold, or turn on Validate cuts with DINOv3 AI. Flashes and strobes are the usual cause. Lots of tiny clipsRaise min_scene_len, so very short pieces join their neighbours. No chapter foldersSplit into chapters needs chapter markers inside the movie file. If yours has none, leave it off and sort the clips into chapter folders yourself."
},
{
"part": "The pipeline",
"title": "7 Depth Map: DepthCrafter",
"href": "part2.html#depthcrafter",
"text": "Depth Map is the first numbered stage. It has two tabs, one per model: DepthCrafter, described here, and VDA (Video Depth Anything), described at the end of this section. DepthCrafter looks at each clip and makes its depth map: a black-and-white video of the same shot where white is near and black is far. Everything after it builds the 3D from this map, so a good depth map matters more than anything else in the pipeline. ReadsYour clips, one per shot, from the chapter's Source folder. WritesOne depth map per clip into the chapter's DepthMaps folder, named after the clip with _depth added: chase_001.mp4 gives chase_001_depth.mp4. ThenEach clip it has finished moves into finished inside the Source folder (Part 1, section 4), where Splatting picks it up. A clip that fails moves into failed instead, so you can see which ones need another look. Untick Resume (in Frame & Segment Control) to leave the clips where they are; Splatting then needs its Source Clips pointed at the Source folder itself. Quick startPick the chapter, press Select Preset… and choose the 768-wide size in the column that matches your movie's shape, then press Start. The other settings start out at good values. 1 2 3 4 5 6 7 8 DepthCrafter with chapter 3 picked."
},
{
"part": "The pipeline · 7 Depth Map: DepthCrafter",
"title": "1 Input Source",
"href": "part2.html#depthcrafter",
"text": "With a chapter picked, the folders are filled in for you and folded away; Change Folders shows them. On Manual paths you set them yourself with Browse, which opens Studio's folder picker: each level of folders side by side, each folder showing how many clips it holds, and the open folder's clips in the last column. Click a folder to open it, double-click it (or press Select Folder) to use the whole folder; click a clip and press Select Clip to process just that one. A folder of numbered image frames counts as one video. Every stage's folder fields use the same picker. After Scene Cut has split a movie, Import from Scene Cut appears here and fills the input with its clips."
},
{
"part": "The pipeline · 7 Depth Map: DepthCrafter",
"title": "2 Main Parameters",
"href": "part2.html#depthcrafter",
"text": "Resolution Preset: the size the clips are worked on at. Studio comes with no presets: you make your own in Settings (below), a column per aspect ratio, each size with how far it is from the true shape, for example 640×448 (7.1% wider). Until you do, the menu says where to make them. Press Select Preset… and pick a size; a column with more than three sizes scrolls. Start with 768 wide (see the note below). The preset fills in Target Width and Target Height; if you type your own, both must be multiples of 64. Once a clip is loaded, the menu shows only the sizes for that clip's shape, with its Auto Size pick. Auto Size for the Loaded Clip (off until you tick it): loading a clip fills Target Width and Height with the size picked for its shape -- 768 wide, with the height (a multiple of 64) nearest the clip's shape, unless you've picked another for that shape in Settings (below). The picked size is marked auto in the Resolution Preset menu, and you can still change it. In a folder of mixed shapes, the clips shaped like the loaded one run at Target Width and Height, and every other clip runs at its own shape's pick. Inference Steps: how many passes the AI makes over each window. See the note below. Guidance Scale: leave it at 1. It's an advanced control, and Beginner Mode hides it. Seed: the AI's starting point. The same seed and settings give the same depth map again; -1 picks a new one every run. CPU Offload Mode: saves graphics memory by keeping parts of the AI in normal memory until they're needed. model suits most cards. If you run out of memory, try sequential (slower); with lots of memory, none is fastest. The preset menu on a 2.39:1 clip: only the sizes for its shape, with the Auto Size pick marked auto. Sizes you tick in Settings > DepthCrafter > Custom Resolution Presets join it, a column per shape. Start at 768Most scenes are perfectly fine at 768 wide, and it still picks up fine detail like particles, snow and sparks. Bigger isn't better here: a size that's too high can exaggerate the separation between things and put them at the wrong depth. Only move up to 960 or 1152 when a scene needs more detail than 768 gives, and check the result. Bigger sizes also take more graphics memory and time. Inference Steps5 is a good starting point for testing: you get a result quickly. For your real runs, 10 to 15 is recommended. 25 is the most the model takes. Going higher can sometimes improve quality, but too many steps can introduce artifacts of their own. The same advice holds for Inpainting."
},
{
"part": "The pipeline · 7 Depth Map: DepthCrafter",
"title": "3 Frame & Segment Control",
"href": "part2.html#depthcrafter",
"text": "Window Size: how many frames the AI looks at together. This is what makes DepthCrafter a temporal model: within a window, the depth stays steady from frame to frame. Bigger windows are steadier but need more memory; 60 to 120 is typical, and Studio starts at 60. Overlap: how many frames each window shares with the next, so the depth blends where they meet instead of jumping. Studio starts at 45; more blends more smoothly and takes longer. It must be smaller than the window. Save Checkpoint: saves the AI's slow work on each clip once it's done, before the depth video is written. If something goes wrong after that, or you run the clip again with the same settings, Studio offers to pick up from there instead of doing the slow part again. On from the start: it costs a little disk space while a clip runs. Target FPS and Process Max Frames: leave both at -1 (the clip's own frame rate, every frame). Anything else makes a depth map that doesn't line up frame for frame with the clip. Beginner Mode hides both, and keeps Target FPS at -1. Save Sidecar JSON for Final Output: a small file beside each depth map recording the settings used. It doesn't change the depth map. Process as Segments (Low VRAM Mode): for very long clips or small graphics cards. See below."
},
{
"part": "The pipeline · 7 Depth Map: DepthCrafter",
"title": "4 Output Processing",
"href": "part2.html#depthcrafter",
"text": "Stabilize Depth Scale: each window is worked out on its own, so over a long clip the whole scene can slowly drift nearer and further, like it's breathing. This evens it out against one reference frame. Turn it on for clips longer than one window; it does nothing useful on short ones. Percentile Output Norm: one stray bright or dark speck can squash the depth of everything else. This ignores the most extreme pixels when setting the depth range, for better contrast. Its Auto Top switch is for clips where something comes right up to the camera for a moment (a person walking past the lens): the range is then set from the frames that show the usual scene, so the passer-by clips to white and the scene keeps its full depth instead of being squeezed dark. A clip without such a moment comes out the same. Encoder CRF / Preset: quality and file size. Leave them on auto and veryslow; saving is quick next to the depth work. Output Format: main10_mp4 (the default) stores finer depth steps than plain mp4, so smooth surfaces don't show bands. The image-sequence formats save one picture per frame and take much more space."
},
{
"part": "The pipeline · 7 Depth Map: DepthCrafter",
"title": "5 Secondary Output",
"href": "part2.html#depthcrafter",
"text": "Makes a second depth map for each clip, with its range set more firmly so things entering or leaving the frame don't shift the rest of the depth. It's an extra for experimenting; most people leave it off."
},
{
"part": "The pipeline · 7 Depth Map: DepthCrafter",
"title": "6 Current Clip Info",
"href": "part2.html#depthcrafter",
"text": "The clip being worked on, its size and frame count, and how far through the batch you are. Open Console shows the detail. While it runs, the line under the progress bar reads Processing video 3 of 20, adding segment 2 of 4 when the clip is done in segments."
},
{
"part": "The pipeline · 7 Depth Map: DepthCrafter",
"title": "7 Upcoming Jobs",
"href": "part2.html#depthcrafter",
"text": "To the right of the settings: the clips in the input folder that Start will process, each with its own bar while the batch runs. Tick clips and press Process Selected to do just those, or right-click one for Process This Clip Now, Move to Finished or Play Source Clip (see Upcoming Jobs)."
},
{
"part": "The pipeline · 7 Depth Map: DepthCrafter",
"title": "8 The action bar",
"href": "part2.html#depthcrafter",
"text": "While a clip runs, the ring shows the current window, with the time each step takes beside it, and the bar shows the clip's progress. The TIP line above it offers a hint now and then. Start works through every clip in the input. Cancel asks how to stop: now, losing the clip in progress, or after this segment, keeping what's finished so the next run carries on. View Output Folder opens the depth maps' folder, even during a run."
},
{
"part": "The pipeline · 7 Depth Map: DepthCrafter",
"title": "Process as Segments",
"href": "part2.html#dc-segments",
"text": "Normally a clip is worked on as one piece. With Process as Segments on, a long clip is split into pieces, each piece gets its own depth map, and the pieces are joined back together. Use it for very long clips, or when your graphics card runs out of memory. 1 2 1 Merged Output Options, how the pieces are joined. 2 The segment tools. Alignment Method: how neighbouring pieces are matched where they overlap. Shift & Scale fits each piece to the one before; Linear Blend fades between them. If the depth jumps where pieces join, try the other. Normalization, Dithering and Gamma Adjust fine-tune the joined depth map: ignoring extreme specks, hiding banding, and pulling the middle of the scene nearer or further. Keep intermediate NPZ keeps each piece's raw depth after joining, so you can join them again later with different options. Output Suffix: leave it as _depth. Splatting looks for that name. Re-Merge Segments joins a finished clip's pieces again with the current options, without redoing the slow depth work (it needs the kept NPZ files). Generate Seg Visuals saves a viewable copy of each piece, to check one that looks wrong."
},
{
"part": "The pipeline · 7 Depth Map: DepthCrafter",
"title": "In Settings",
"href": "part2.html#dc-segments",
"text": "With the DepthCrafter tab showing, Settings lists its extra options: Load Settings / Save Settings As, to keep a set of settings for a kind of footage, and Reset to Default. Restore Finished / Failed Input Files moves clips back out of finished or failed, so you can run them again. Use SageAttention (Faster) speeds the AI up on cards that support it. Use Local Models Only skips the online check for the model files at the start of a run, once you have them. Enable Debug Logging adds detail to the console, for troubleshooting. Custom Resolution Presets, under those options, makes your own sizes for the Resolution Preset menu. Pick an Aspect ratio, then open Sizes and tick the ones you want. Every size there is a multiple of 64, as DepthCrafter needs, from 256 wide up to about 1080p; at each width you get the heights just under and just over the true shape, each with how far off it is (exact, 7.1% wider, 6.2% taller). For an exact size of your own, type it into Add a size (width × height, both multiples of 64) and press Add: it's ticked and listed with the rest. Tick sizes in as many ratios as you like, then Save Presets: they appear in the menu at once, a column per ratio. Untick a size and save to take it out again; Remove All Presets (click it twice) empties the menu. The same window holds the Auto Size picks: for each aspect ratio, the size Auto Size for the Loaded Clip uses for a clip of that shape. Each shows its default (768 wide) until you choose another."
},
{
"part": "The pipeline · 7 Depth Map: DepthCrafter",
"title": "When something isn't right",
"href": "part2.html#dc-segments",
"text": "Out of memoryPick a smaller preset, lower Window Size, set CPU Offload Mode to sequential, or turn on Process as Segments. Close other programs that use the graphics card. Depth slowly breathesTurn on Stabilize Depth Scale. Depth jumps partway throughRaise Overlap, so windows blend over more frames. Everything looks flatTurn on Percentile Output Norm: a few extreme pixels are probably squashing the range. Bands on smooth surfacesUse main10_mp4 as the Output Format. Not enough detailCheck Inference Steps is at 10 to 15, not the quick-test 5. Only if the scene still needs it, try the next preset up (960, then 1152). Separation looks exaggerated, or things sit at the wrong depthThe preset is probably too high for the scene. Go back to the 768-wide one. The depth mixes two scenesThe clip has a cut in it. Cut it into one clip per shot (section 6). Good to knowWhile DepthCrafter runs, Studio keeps about 3 GB of your graphics card free, so Windows and Studio's own window stay responsive."
},
{
"part": "The pipeline · 7 Depth Map: DepthCrafter",
"title": "Video Depth Anything (the VDA tab)",
"href": "part2.html#vda",
"text": "DepthCrafter's page has two tabs at the top. The second, VDA (Video Depth Anything), makes depth maps with a different, much faster model: seconds per clip instead of minutes. Its maps are sharp at the edges and steady over time, and go where DepthCrafter's go, under the same name, so Splatting reads them the same way. Try it first; keep DepthCrafter for the shots it gets wrong. It has its own Input Folder/File and Output Folder (a chapter fills them as it fills DepthCrafter's), its own Upcoming Jobs and its own Resume. Image-sequence folders are DepthCrafter's: this tab takes videos. ModelIn the Video Depth Anything section. Small is quickest, Large the most detailed, Base in between. Each is its own download: Download Weights appears on the tab when the one picked isn't there, or use Settings > Studio > Models. Small is free for any use; Base and Large are for non-commercial use only (their licence, CC-BY-NC-4.0). Input SizeThe short side of the picture the model looks at, a multiple of 14. 518 is what it's trained at. Higher keeps finer detail and needs more time and memory. The map is written at about this size; Splatting scales it to its own. ModeOnline runs the clip in overlapping 32-frame windows, each joined to the one before, so it needs the same memory however long the clip is. Offline is the model's own reference way and holds the whole clip in memory: for short clips. Half Precision (FP16)About twice as fast; the maps look the same. NormaliseWhole clip spreads the clip's nearest-to-farthest over the whole black-to-white range once, as DepthCrafter does, so a thing keeps its depth however the rest of the shot changes. EMA lets the range follow a long shot whose depth really changes, eased by Decay and looking Buffer frames ahead. Replacing a DepthCrafter map. When the output folder already holds a DepthCrafter map of the clip, it moves into a DepthCrafter folder inside the output folder, and the new map takes its place, so Splatting shows the new depth straight away, with a VDA badge on the clip in its list. Nothing is lost: with Splatting's Multi-Map on, that folder is one of the maps it offers. A later Video Depth Anything run of the same clip simply replaces its own earlier map. In Splatting, right-click a clip in Upcoming Jobs and choose Rerun with Video Depth Anything: this tab opens with that clip as its input and the clip's depth-map folder as its output. Press Start. While it runs, the console's Depth Map tab shows each clip's progress as a bar: the depth pass, then the encode, with frames done, speed and time left. Good to knowLarge at 518 needs about 12 GB of graphics memory on a 1920×800 clip. Short on memory? Pick a smaller Model or a lower Input Size."
},
{
"part": "The pipeline",
"title": "8 Splatting",
"href": "part2.html#splatting",
"text": "Splatting turns each clip and its depth map into the second eye's view. Every pixel is shifted sideways by how near it is: near things move a lot, far things hardly at all. This is where your main 3D effect editing happens: how deep the 3D is, where the screen sits, how clean the edges are, and the Special FX. ReadsThe clips DepthCrafter has finished (in the Source folder's finished) and their depth maps from the DepthMaps folder. Each depth map must be named after its clip plus _depth. WritesInto the chapter's Splats folder: a full-size result in hires and a smaller copy in lowres for Inpainting. Both still have holes where the shift uncovered something the camera never saw; Inpainting fills them next. Per clipThe settings you choose for a clip can be saved with it in a small sidecar file (8f), so every clip can have its own 3D. Quick startPick the chapter and click the preview to load the first clip. Put on red/cyan glasses (the preview shows an anaglyph), set Disparity for how deep it should feel and Convergence for where the screen sits, press Create Sidecar, and move on to the next clip. When every clip looks right, press Start. 1 2 3 4 5 6 7 8 9 1 Folders · 2 the preview · 3 Process Resolution · 4 Splatting & Output Settings · 5 Depth Map Pre-processing (Edge Alignment) · 6 Stereo Projection · 7 Special FX · 8 Upcoming Jobs · 9 the action bar."
},
{
"part": "The pipeline · 8 Splatting",
"title": "8a Folders and the clip list",
"href": "part2.html#splat-folders",
"text": "With a chapter picked, the folders fill in by themselves. The folder panel also holds Encoding Options… (how the results are saved, 8g), the 8-bit / 10-bit buttons that set it in one click, and Multi-Map (8f). Upcoming Jobs lists the chapter's clips. Click a clip to load it into the preview; right-click it for Splat This Clip Now, Rerun in DepthCrafter or Rerun with Video Depth Anything (see Upcoming Jobs). Each clip shows a still, its length, and two bars that fill as its full-size (H) and smaller (L) results are made. Tick boxes pick clips. Select All and Deselect All do it in one go. Process Selected converts only the ticked clips; Move to Finished moves them into finished without converting them, for clips you've decided not to use. Little badges on a clip show what's set for it: a sidecar, Pop Out Boost, Keyframes, Frame Break, Rain (three blue streaks), a depth map edited in the Depth Map Editor (a pencil), a depth map made by Video Depth Anything (VDA), or a saved Detect Text mask (a T), or an object taken out with Erase from Clip (a violet circle struck through -- Merging's list shows it too, where Put Back lays it back). The sidecar's badge sits beside the progress bars; the others on a line under the clip's name, so a row with any of them is a little taller. A legend under the list says which is which. Start converts every clip in the list; Single converts only the one in the preview. To convert just some of them, tick those clips and press Process Selected. Resume (in Stereo Projection): finished clips move into finished, so if you stop and start again, the batch carries on with the rest. Leave it on."
},
{
"part": "The pipeline · 8 Splatting",
"title": "8b The preview",
"href": "part2.html#splat-preview",
"text": "The preview shows a frame of the clip exactly as it will be converted, and updates as you change settings. It shows two views, Front and Back: click the picture to flip between them, or grab its left or right edge and drag it across to turn it over by hand (let go before halfway and it falls back). Every view, what it shows and what it's for: Appendix D. Dubois Anaglyph (the usual Front) is red/cyan 3D: wear red/cyan glasses to judge depth on any screen. Other choices include plain side-by-side, cross-eye, a Wigglegram that rocks between the eyes, the depth map, and Splat Result: the new eye on its own (the usual Back). Glasses-Free 3D needs a glasses-free 3D monitor on Leia's SR platform (Samsung's Odyssey 3D, for one), with Studio's window on it; without one it is greyed out, and its tip says what's missing (its caveats: Appendix D). Only the picture turns 3D, with each eye at full resolution; the rest of the window stays as it is. The 3D follows your eyes while nothing is running. While a job runs, it holds the last frame and the eye-tracking camera stays off. Choose another view and the camera and the 3D lens switch off. Tick Keep Tracking (beside the view, while Glasses-Free 3D is chosen) to keep the camera on between clips, in and out of Full Preview and while a job runs, so the 3D doesn't have to find you again each time. It still switches off on another tab or view, and when Studio closes. Full Preview: the full-screen icon in the preview's top-right corner (or F11) shows the preview full screen, for watching on a 3D TV or monitor. It opens on the screen Studio is on the first time; move it to another with Win+Shift+←/→ and it opens there from then on. With Glasses-Free 3D facing you, its picture is the one shown in 3D while it is open; the page's picture shows it again once it is closed. Esc or F11 closes it, or the X in the top-right corner. The X and a frame slider near the bottom show for a moment when it opens and again whenever you move the mouse (in Glasses-Free 3D too, over the 3D picture); the playback keys work there as on the page. In Splatting, Convergence, Disparity (Depth Strength) and Gamma dials float under the slider, so you can tune the 3D while you watch. They are the page's own dials: keyframes work on them as on the page. The frame slider, ‹ Prev / Next › (clips), play and fast-forward move around. Keys: ← → a frame (Shift for 10), ↑ ↓ the next or previous clip, Space play. Double-click to zoom in on a spot, and again to see the whole frame; the mouse wheel zooms too. Preview Scale sets the size: Fit (the first choice, and how Studio starts) fills the space the preview has and keeps it filled as the window or the clip changes, up to 250%; pick a percentage, or zoom with the wheel or a double-click, to hold a size instead (a double-click on a zoomed-in picture goes back to Fit). Preview Brightness lightens dark scenes (double-click it, or right-click it, to go back to 1.0), and Crosshair marks the screen centre, which helps with Convergence. In Glasses-Free 3D it is drawn in both eyes, so it sits at screen depth. None of these change the saved files. Splat Result with Highlight Holes on (and the 3D turned up so they show). The coloured bands beside the three of them are the holes: places the second eye sees that the camera never did. Inpainting paints them in."
},
{
"part": "The pipeline · 8 Splatting",
"title": "8c The 3D look",
"href": "part2.html#splat-depth",
"text": "Stereo Projection sets the 3D itself: Disparity (Depth Strength): how strong the 3D is. Higher gives more depth, but also bigger holes to fill, and very high values are hard on the eyes. The ≈ button beside the knobs measures how strong it really gets across the clip. Convergence: where the screen sits in the scene. 1.0 puts everything behind the screen, like looking through a window; lower values bring things out toward you, and 0.0 makes everything pop out. 0.8 is a good start. Gamma: reshapes the middle distance. Above 1.0 brings it nearer, below 1.0 pushes it back. Pop-Out: more pop for whatever is in front of the screen, with the rest of the scene left where it is. Everything at or behind the screen stays exactly put, subjects just in front of it barely move, and the nearest things get the full extra. It's spread over the whole front of the scene, so it bends outlines much less than Pop Out Boost for the same pop. It goes up to 60, in the same units as Disparity. It's saved with the clip and can be keyframed. Border Width and Border Bias (shown with Border Settings): black edges at the sides of the frame, so things that pop out aren't cut off by the edge of the screen. The Border setting in Splatting & Output Settings can work them out for you (Auto Basic, Auto Adv.). Dual Eye builds both eyes from your video and splits the shift between them, so each eye has smaller holes. Flip mirrors a clip where things move left to right; Merging flips it back at the end. Depth Map Pre-processing (Edge Alignment) tidies the depth map before the 3D is built, so depth edges line up with the edges of objects and outlines don't shimmer: Dilate X / Y (Expand X / Y) grow near objects' outlines in the depth map, so their 3D edge covers the whole object (below 0 shrinks them). Blur X / Y soften depth edges, smoothing jagged or flickering outlines. Too much makes edges soft. Dilate Left (Expand Left), Blur Left and Blur Left Mix do the same for left-facing edges only."
},
{
"part": "The pipeline · 8 Splatting",
"title": "8d Special FX and keyframes",
"href": "part2.html#splat-fx",
"text": "Special FX: Pop Out Boost, Keyframe and Frame Break open. Pop Out Boost: use the Pop-Out dial in Stereo Projection (8c) first, for a clean enhancement of foreground elements. Boost is meant to work together with that dial to create \"right in front of your face\" pop when the dial alone isn't enough. It pushes only what is nearer than a set depth further out of the screen, and it has a higher risk of warping the edges of objects. Strength (up to 60, in the same units as Disparity) turns it on; Threshold sets how near something must be, and Curve how sharply it ramps up. Keyframe lets settings change partway through a clip. Set things up at one frame and press Add Static Keyframe (the values hold from there until the next keyframe) or Add Dynamic Keyframe (the values glide to these from the keyframe before); move on and do it again. Once a clip has keyframes, the dials show the values at the frame you're on, playing too. Turning a dial on a keyframe changes that keyframe. Turning one on a frame without a keyframe adds a dynamic keyframe there, with your change and the other dials as they were showing; Clear Current takes it off again. The Shaper under the keyframe buttons sets how the values glide into a dynamic keyframe. Put the playhead in the stretch that ends on it, then pick a Shape: Linear (a steady ramp, as before), Ease In, Ease Out or Ease In-Out; an LFO -- Sine, Triangle, Square, Saw or Random -- swinging back and forth between the two keyframes' looks for Cycles swings before it lands; or Custom. In the graph the bottom is the keyframe before, the top this one, and the line the playhead. For Custom, click to add a point, drag to move it, and right-click or double-click to remove it. Hover a stretch of the line for its orange handle: drag it up or down to bend that stretch, left or right to move the bend; double-click it to straighten it. Every shape lands exactly on the keyframe, and the shape is saved with the keyframe in the sidecar. Frame Break adds letterbox bars that near objects can break out of, for a popping-out-of-the-frame effect. Bar Size sets the bars (the Letterbox Size buttons match standard shapes), and Break Thr how near something must be to cross them. One scene at a timeSpecial FX are meant for a single shot. Save them in the clip's sidecar, and set Strength or Bar Size back to 0 for the next clip. Closing a section only hides it; the heading says on while it still applies."
},
{
"part": "The pipeline · 8 Splatting",
"title": "Rain",
"href": "part2.html#splat-particles",
"text": "Rain, open. DepthCrafter struggles to detect rain, so in a plain splat the rain follows the shape of whatever object it falls in front of: the drops shift with the wall, not in front of it. Rain takes each frame's rain out, using the frames around it, warps the scene without it, then warps the rain on its own at depths of its own and adds it back to both eyes. (Most other particles -- snow, sparks, dust -- DepthCrafter picks up by itself; rain is the one it sees through.) They go through the same Disparity, Convergence, Pop-Out and Pop Out Boost as the scene, so the nearest drops come out the way the nearest objects do. Everything here is saved with the clip, and a clip with Rain on shows three blue streaks in Upcoming Jobs once its sidecar is saved. Rain needs tuning for each scene, so Beginner Mode hides it. Rain off Rain on A rainy street in the Glasses-Free 3D / Dubois Anaglyph preview (Static camera, Downpour, In front). With Rain off the streaks are part of the picture and take the depth of the street and walls they fall in front of; with it on, they come off the scene and are laid back in front of it, on layers of their own. The switch turns Rain on or off for the clip. Beside it, the camera: Moving lines the neighbouring frames up on each frame first (any shot, camera or people moving); Static is for a locked-off shot, simpler and a little cleaner. Scenario: Downpour for sharp streaks -- a storm, a lit downpour; Soft (out of focus) for wide, faint rain, in close-ups or behind a shallow focus; Mist (dense fine) for a dense sheet of fine rain in a backlit haze. Soft and Mist work best with Moving. Place: where the drops go in depth. In front puts them on planes from the middle of the scene out toward you, the same all over the frame -- rain in the air between you and the scene. In place puts each one at the scene's own depth where it is, lifted a little by its layer -- rain falling far off stays with what's behind it instead of floating in front. Any direction: for rain that doesn't fall one way, looking up into it or coming at the lens. Slower. Keep wires & lines out (on by default): long, thin, bright things that run on through the frame -- wires, cables, ropes, a kerb's edge -- stay with the scene. Keep lights out (on by default): lamps, lit windows and signs stay with the scene. When the camera moves, a light lands a little off in the frames around it, and what is left over can be taken for a drop; no rain is taken on or right around a light. (Guard doesn't help there: it only protects what is near you.) Guard: takes no rain where the scene is near -- people standing in the rain, whose clothes' moving reflections can look like rain. 0 is off. Grow: grows (+) or shrinks (−) the cut-outs, in pixels at 1080p. Grown, they take in their glow and tips, but never more light than the frame has there, so no dark edges are cut into the scene; shrunk, their edges stay painted on the scene. Depth: how strongly the drops get depths of their own. 0 leaves each one at the scene's depth right where it is, the same as Rain off; 1 is the full placement (In front: the nearest a whole scene's depth in front of the scene's nearest part). Amount: how strongly they go back in -- 1 as they were, less to thin them, more to bring them out. Layers: how many depth planes they're spread over (1 to 10). Rain is shared out by streak length, the longest streaks on the nearest plane. Pop: extra Pop-Out for the rain alone, in the same units: the nearest drops get all of it, farther layers less, and the scene none. The ≡ button beside the camera opens the layer controls: Spread, the distance between the layers (1 as it was, lower draws them together toward the nearest plane -- 0 puts them all on it -- higher spreads them further back, up to twice as far); Spacing, how they share that distance (1 even, higher crowds them toward the nearest plane, lower toward the farthest); and Convergence, the rain's own screen plane, from the scene's (0 the scene's; + puts them further back, − brings them out, the scene staying where it is). With In place, Spread sets how far the layers lift off the surface. Any direction off Any direction on Rain coming at a camera that looks down on it: the streaks point every way, so the usual search, for rain falling one way, misses most of them. With Any direction off they stay with the street, behind the screen; on, they're found and come out in front of it. Checking what it takesThe preview shows exactly what is taken: turn Rain off and on to compare."
},
{
"part": "The pipeline · 8 Splatting",
"title": "8e Fixing problem areas",
"href": "part2.html#splat-fix",
"text": "Hole Shift opens a window showing the frame's holes. Click the ones to move and nudge them with the X and Y knobs, without changing their shape. Detect Text masks burned-in subtitles and captions so they stay flat and readable instead of being warped into the 3D. See below. Edit Depth Map opens the clip in the Depth Map Editor, to fix the depth of an object by hand (Part 3)."
},
{
"part": "The pipeline · 8 Splatting",
"title": "Detect Text",
"href": "part2.html#detect-text",
"text": "Load the clip in the preview and press Detect Text. A window opens on the same frame. The picture Mask ticked Detect Text on a title card: a box drawn round the title, and two selections that cover the frames it is on (0–41 and 42–83). Tick Mask to see only what will be kept flat -- here the letters, cleanly, and nothing of the city behind them. Untick it to go back to the picture. Draw a box over where the text sits. Only text inside it is picked up. Tune what counts as text with Core and Edge (and Refine), watching the mask on the picture. Zoom Rect zooms in on the box. Set the frame range. Step to the first frame with the text and press Set From (or I), then to the last and press Set To (or O). The slider moves one frame at a time, and the range is marked on it. Press Save. Studio writes the mask as a video beside the clip (<clip>_submask.mp4), and Splatting uses it from then on. The mask video is never treated as a clip of its own. Several bits of text in one clip? Press + Add for each one. Every selection has its own frame range, box and Core / Edge settings; click its chip to edit it. Save puts them all into the one mask. Text that fades in or out loses the detection as it fades. Find a frame where it's caught well and press Freeze Here: that frame's mask is used, at full strength, for the whole selection. Click it again to unfreeze. Lots of text, like opening credits? When the list gets long, press Flatten Layers. It saves the mask with every selection so far, then sets them aside: they stay in the mask, and you still see them on the picture and as faint marks on the slider, but they leave the list. The next selection you add starts fresh. Flatten as often as you like. Remove deletes the current selection. With none left, Save becomes Remove Mask and takes the clip's mask away. Reset All (it asks first) starts the clip over: every selection, range, freeze and setting is erased. Once a clip has a saved mask, four more knobs appear in Splatting, next to Dilate and Blur. They only affect the text, and they're saved with the clip: Text Grow widens the mask by that many pixels (below 0 shrinks it). Text Soften feathers its edge, so the flat text blends into the 3D around it. Text Clean takes DepthCrafter's own copy of the text out of the depth map, filling it from the background around it, so its warped edges don't stick out past the mask. It's how far past the mask to clean, in pixels; 0 is off. Start around 8 to 12, and raise it if warped edges still show around the letters. Text Conv sets where the text sits in depth: its own Convergence, as an offset from the clip's. At 0 it sits flat on the screen. Above 0 sets it back behind the screen, below 0 brings it out toward you, the way the Convergence knob moves the scene. Small values go a long way."
},
{
"part": "The pipeline · 8 Splatting",
"title": "8f Sidecars and Multi-Map",
"href": "part2.html#splat-sidecars",
"text": "A sidecar is a small file saved beside a clip's depth map (ending in .fssidecar) that holds that clip's own settings: Disparity, Convergence, borders, Special FX, keyframes. Create Sidecar (then Update Sidecar) saves the preview's settings for the clip showing. When the batch reaches that clip, it uses them, and so do the later stages. That's how you give every shot its own 3D: go through the clips in the preview, set each one, save its sidecar, then convert them all with Start. Multi-Map lets you keep several versions of the depth maps in subfolders of the DepthMaps folder and switch between them per clip, to compare and pick the best."
},
{
"part": "The pipeline · 8 Splatting",
"title": "8g Output",
"href": "part2.html#splat-output",
"text": "Process Resolution decides what's made: Enable Full Res: the full-size result, in hires. Inpainting and Merging use it to keep the final picture sharp. Enable Low Res (on from the start, as Full Res is): the smaller copy Inpainting works on, in lowres, at the Width and Height set below it. Bigger keeps more detail but makes Inpainting slower. The preset button next to them works like DepthCrafter's, with your own sizes from Settings > Splatting > Custom Resolution Presets: the same editor as DepthCrafter's, suggesting four sizes for each shape -- 512, 640, 768 and 960 high, each with the width nearest the shape -- and taking any exact size you type in, in multiples of 16, which both Inpainting versions can use. If you type your own size into Width and Height, keep both numbers multiples of 16. It's only the size the fill is made at: Inpainting's Hi-Res Blend puts it back on the full-size picture. Auto Size for the Loaded Clip (off until you tick it): loading a clip into the preview fills Low Res Width and Height with the size picked for its shape (the Auto Size picks in that same Settings editor; 768 high by default). In a batch, every clip of another shape runs its Low Res pass at its own shape's pick, so a chapter that switches aspect ratios gets the right size for every clip. Both are on from the start: keep them that way for the normal pipeline (Beginner Mode does this for you). Dual Output Only, also on from the start, keeps just what Inpainting needs for the right eye, to save space; ffmpeg (on too) decodes the clips with FFmpeg, so the colours match what Resolve or VLC show (a little slower). Batch: how many frames go to the graphics card at once. Higher can be faster but needs more memory. 10 is a safe full-size batch; on a 16 GB card, keep full-size batches to about 20 and smaller ones to about 30. Low Res's preset button on a 2.39:1 clip: the menu offers only the sizes for the loaded clip's shape, and the Auto Size pick is marked auto (1840×768, 0.2% wider than the true ratio). Picking one fills Width and Height. Settings > Splatting > Custom Resolution Presets: pick an aspect ratio, tick the sizes you want in its menu (or type your own, in multiples of 16, and press Add), and choose the ratio's Auto Size pick. Every size ticked so far is listed under it; Save Presets keeps them. Splatting & Output Settings holds the choices you make once for a project: Mask and Warp (how holes are found and how the picture is shifted; SC is the standard), Auto-Converge (works out Convergence for you), Border mode, and the Mesh settings for the Mesh Warp preview. Encoding Options… sets how the files are saved; the defaults are high quality. Full Res CRF and Low Res CRF set the quality of each result (lower is better quality and bigger files). Enable DNxHR saves the full-size result as DNxHR (a .mov made for editing) instead of H.264 / H.265, so the picture stays visually lossless as it passes through Inpainting and Merging. The files are very large: several GB per minute. Container: the file type of the H.264 / H.265 results, MP4 (the default) or MKV. An MKV cut short by a crash or a full disk still plays up to where it stopped. Inpainting and Merging read either, and they have the same choice in their own Encoding Settings. With Enable DNxHR on, the files are always .mov. DNxHR Profile: match it to your source. 8-bit (SQ / HQ) suits 1080p Blu-rays and web rips; 10-bit (HQX / 444) is for HDR and 10-bit SDR sources, such as 4K Blu-rays."
},
{
"part": "The pipeline · 8 Splatting",
"title": "When something isn't right",
"href": "part2.html#splat-output",
"text": "Rain looks painted on the wallTurn on Rain with Rain, and pick the Scenario that matches it. Rain barely picked upTry another Scenario (Soft for faint, out-of-focus rain; Mist for a dense haze), or Any direction. Lamps or lit windows taken as rainKeep Keep lights out on. Guard won't help: it only protects what is near. Wires, cables or clothing taken as rainKeep Keep wires & lines out on; raise Guard for people standing in the rain. Far-off rain floats in front of what's behind itSet Place to In place. Boosted objects' outlines bendLean on Pop-Out and turn Pop Out Boost's Strength down. Boost catches things you didn't meanDepth alone can't tell two things at the same depth apart. Select just the object in the Depth Map Editor (Part 3) and push it nearer. 3D is uncomfortable to watchLower Disparity, or raise Convergence so less pops out of the screen. Outlines shimmer or look raggedRaise Blur X / Y a little, or Dilate X so the depth covers the whole object. Pop-out objects cut off at the frame edgeAdd Border Width, or let Border work it out (Auto). Subtitles warpDetect Text (8e). Warped edges around masked textRaise Text Clean, or Text Grow if the mask misses part of the letters. An object sits at the wrong depthFix its depth in the Depth Map Editor (Edit Depth Map, Part 3). Out of memory, or very slowLower the Batch sizes. Too big a batch spills into normal memory and slows to a crawl before failing."
},
{
"part": "The pipeline",
"title": "9 Inpainting",
"href": "part2.html#inpainting",
"text": "The Splatting stage creates holes in the frame where the image was warped. Inpainting uses generative AI to fill in those gaps for a much cleaner right eye view. It looks at the frames around each hole, works out what was probably behind the object, and paints it in, keeping it steady from frame to frame. ReadsSplatting's smaller results from the chapter's lowres folder (the AI works at that size), and the full-size ones from hires to blend the fills into the full-size picture. WritesThe filled right eye for each clip into the chapter's Inpainted folder. ThenEach finished clip's Splatting results move into finished, ready for Merging. Quick startPick the chapter (or press Import from Splatting) and press Start (Inference Steps starts at 10). Inpainting is the slowest stage, so a whole chapter is a good overnight run. 1 2 3 4 5 6 7 1 V1 / V2 · 2 Folders · 3 Parameters · 4 Post-Processing · 5 Current Clip Info · 6 Upcoming Jobs · 7 Progress and the buttons."
},
{
"part": "The pipeline · 9 Inpainting",
"title": "1 V1 or V2",
"href": "part2.html#inpainting",
"text": "The tab holds two inpainting engines. V1 (SVD) is the standard one, and everything in this section is about it. V2 (Wan VACE) is experimental and meant only for problem scenes (below)."
},
{
"part": "The pipeline · 9 Inpainting",
"title": "2 Folders",
"href": "part2.html#inpainting",
"text": "Lo-Res Blend Folder: Splatting's lowres results, the ones the AI fills. Hi-Res Blend Folder: Splatting's hires results. The fills are blended into these, so the finished eye keeps its full sharpness. Output Folder: where the filled clips go. Picking a chapter fills all three. Otherwise Import from Splatting fills the two inputs from the last Splatting run."
},
{
"part": "The pipeline · 9 Inpainting",
"title": "3 Parameters",
"href": "part2.html#inpainting",
"text": "Inference Steps: 5 for a quick test, 10–15 for real runs; 25 is the model's limit, and too many can add artifacts of their own. Frames Chunk: how many frames the AI fills at once. 25 is as high as it goes well; higher and pans can come out scrambled. Lower it only if you run out of memory. Frame Overlap: how many frames each chunk shares with the one before, so the fills blend where chunks meet. Raise it (10 is common) if you see a seam; those frames are filled twice, so it costs time. Original Input Bias: where chunks join, how much to lean on the original picture rather than the frames already filled. Higher invents less; lower keeps the fills more consistent. 0 = off; Studio starts at 0.1. Guidance Scale: how much extra effort the AI puts into each step. Leave it at 1.0: each step is filled once, the fastest and lightest on graphics memory. Above 1.0 each step is filled twice, about 30–45% slower, for results that look almost the same: in busy scenes 1.0 can keep a touch less fine detail, hard to see unless you look for it. Hidden in Beginner Mode. Tile Number: splits each frame into a grid (2 = 2×2) and fills each piece separately, so big frames fit in memory. 2 is the default, and right for 16 GB cards; 1 = no splitting. Inpaint Direction: Forward works through the clip from the start; Reverse from the end; Both does one then the other. CPU Offload: keeps parts of the AI in normal memory to save graphics memory. model suits most cards; sequential saves the most but is slowest; none is fastest. Process Length is for testing: only the first so many frames of each clip. To run just one clip, right-click it in Upcoming Jobs and choose Inpaint This Clip Now. Torch Compile: prepares a faster version of the model when a run starts. The first chunk takes about 10 minutes longer while it does, then the run is roughly 10% faster. Worth it for a long unattended chapter, not for a quick test. The Inpaint … Thresh / Kernel settings shape the outline of the holes the AI fills. Dilate Kernel grows it so the fill covers the very edge of each hole; Blur Kernel softens it. The defaults suit most clips. Guidance Scale and speedAt 1.0 (the default) the AI fills each step once. Any value above 1.0 makes it fill each step twice and compare the two, so every chunk takes about 30–45% longer and uses more graphics memory: a chapter that inpaints in 2 hours at 1.0 takes roughly 2 hours 40 minutes to 3 hours above it. The results look almost the same; in busy scenes 1.0 can keep a little less fine detail, but you'd have to look for it. So leave it at 1.0 for whole chapters, and raise it slightly (1.01 to 1.1) only for a busy shot that looks soft. Values well above that are untested with this model and may add artifacts."
},
{
"part": "The pipeline · 9 Inpainting",
"title": "4 Post-Processing",
"href": "part2.html#inpainting",
"text": "Enable Color Transfer: matches the fills' colours to the original picture. Leave it on. It runs on the graphics card, in full precision, so smooth gradients don't band. Keep Inpaint Cache For Re-Merge: keeps the filled frames so a rerun can redo the finish (Color Transfer, a new Hi-Res Blend clip) without filling again. Takes disk space. Resume: finished clips move into finished, so a stopped run carries on with the rest. Leave it on."
},
{
"part": "The pipeline · 9 Inpainting",
"title": "5 Current Clip Info",
"href": "part2.html#inpainting",
"text": "The clip being filled, its size and length, the overlap and bias it's using, and how far through the batch you are."
},
{
"part": "The pipeline · 9 Inpainting",
"title": "6 Upcoming Jobs",
"href": "part2.html#inpainting",
"text": "To the right of the settings: the Lo-Res splats that Start will inpaint, each with its own bar while the batch runs. Tick clips and press Process Selected to do just those, or right-click one for Inpaint This Clip Now, Move to Finished (the splat, its sidecar and its Hi-Res splat), Play Lo-Res Splat or Play Hi-Res Splat."
},
{
"part": "The pipeline · 9 Inpainting",
"title": "7 Progress and stopping",
"href": "part2.html#inpainting",
"text": "The ring counts the chunks of the current clip, with the time per step beside it; the bar shows the clip. Stop asks how: now, or after this chunk, which keeps the finished chunks so the next Start on that clip carries on from there. While a folder of clips runs, Stop After Clip lets the current clip finish, then stops before the next. Click it again to carry on instead. View Output Folder opens the results, even mid-run. Disk spaceWhile a clip runs, Inpainting saves each finished chunk (about half a gigabyte each on a 1408-wide clip) so a crash can resume mid-clip. They're deleted when the clip finishes, but a long clip can need several gigabytes at once. Keep plenty of free space on the drive, or the saves fail and you lose the ability to resume."
},
{
"part": "The pipeline · 9 Inpainting",
"title": "In Settings",
"href": "part2.html#inpainting",
"text": "Use SageAttention: a faster way to run the AI, on cards that support it. Use Sage Attention (for V2) is the same for Inpainting V2, and Load / Save V2 Settings keep V2's settings in a file of their own. Encoding Settings (how the files are saved, for V1 and V2 alike: 10-bit (Main10) writes 10-bit HEVC; Enable DNxHR, off by default, writes DNxHR .mov files for an all-DNxHR pipeline; Container picks MP4 or MKV for the other files), Load / Save Settings, Reset to Default, Restore Finished (moves clips back out of finished to run them again) and Enable Debugging."
},
{
"part": "The pipeline · 9 Inpainting",
"title": "Inpainting V2 (experimental)",
"href": "part2.html#inp-v2",
"text": "Switch to V2 (Wan VACE) at the top of the tab. V2 is a much larger model and more demanding on hardware, so expect significantly longer wait times. Measured on a 16 GB RTX 5060 Ti (MMGP streaming, 5 steps, 2 tiles per axis): about 10 seconds per frame at 1408x768 per eye, so a 4-second clip takes about a quarter of an hour; 20 steps take about 3.5 times as long. Problem scenes onlyIt isn't practical to attempt a full conversion with all V2 inpainting, even on the biggest cards (an RTX 5090 included). Keep it for problem scenes where V1 simply isn't good enough at any other settings, and use V1 for everything else. Its page is simpler: input and output, chunk and overlap, tiles, steps and seed, and a VRAM / Performance panel to fit it to your card. The offload mode starts at MMGP streaming, which runs on a 16 GB card; check it against yours before starting. Torch Compile there makes the chunks faster after a first one that takes several minutes longer while it compiles: worth it for a long unattended run. It works with the offload modes None, FP8 resident and MMGP streaming; the others run without it. (SageAttention for V2 is in Settings.) Upcoming Jobs beside the settings lists the clips a Start would run, as V1's does: tick some and Process Selected, or right-click one for Inpaint This Clip Now, Move to Finished or, once it's done, Send to Merging. Leave Tiles / axis at 2 on a 16 GB card: at 1, an 81-frame chunk runs out of memory. Tile overlap is 64 by default: about 9% faster than 128, with no visible seam where the tiles meet. More Inference steps cost time in proportion and help little. Stopping and crashes: each chunk V2 finishes is kept as it goes (in the output folder's _inpaint_resume), so a clip you stop -- or one cut off by a crash -- carries on from its last finished chunk at the next Start, and comes out the same as an unbroken run. Only the finished chunks' decoding is done again, a few seconds each. Change a setting that changes the result (chunk, overlap, tiles, steps, seed, prompt or offload mode) and the clip starts over. A finished clip deletes its chunks. What it can't fixV2 fills ordinary holes well, but it can't repair pixels the splat has already stretched: a person seen through thin near bars or a fence, where the depth map gives their edge the bars' depth. Fix the depth there instead (the Depth Map Editor, or less depth dilation in Splatting). More steps or a bigger mask only make V2 invent what was hidden, and get it wrong. Getting its weightsUntil they're on your PC, the top of V2's page offers Download V2 Weights: 46.6 GB from Hugging Face -- Wan 2.1 VACE's text encoder, VAE and tokenizer (11.9 GB) and StereoCrafter2 (34.7 GB), only the files V2 uses. Windows shows that as about 43.4 GB. It asks first, showing the sizes and your free space, then downloads in the background with a progress bar; Stop it any time and the next Download carries on where it stopped. With FP8 resident picked as the offload mode, Download FP8 Model fetches its pre-quantized StereoCrafter2 (17.4 GB more). Inpainting V2."
},
{
"part": "The pipeline · 9 Inpainting",
"title": "When something isn't right",
"href": "part2.html#inp-v2",
"text": "A visible seam where chunks joinRaise Frame Overlap (10 is common). Pans come out scrambledKeep Frames Chunk at 25 or below. Fills look a different colourTurn on Enable Color Transfer. Thin lines left at the edge of the fillsRaise Inpaint Dilate Kernel a little. Out of memoryRaise Tile Number, set CPU Offload to sequential, or lower Frames Chunk. Too slowTurn on Use SageAttention; for a long run, Torch Compile. \"Failed to save chunk checkpoint\" (V2: \"Could not keep chunk checkpoint\")The drive is full. Free some space; the run carries on, but can't resume from the chunks it couldn't save."
},
{
"part": "The pipeline",
"title": "10 Merging",
"href": "part2.html#merging",
"text": "Merging is the last stage. It takes Inpainting's filled-in right eye and blends just the filled areas into Splatting's full-size right eye, so everything that didn't need filling keeps its full sharpness. Then it puts the left and right eyes together into the finished 3D video. ReadsThe filled right eyes from the Inpainted folder, your original clips (the left eye) from the Source folder's finished folders, and Splatting's full-size results from hires, which carry the outline of the holes (the mask). WritesThe finished 3D video for each clip into the chapter's Merged folder. ThenWith Resume on, each clip's inputs move into finished. No original, no startA clip inpainted from a two-picture splat has no left eye of its own: Merging takes it from your original clip in the Original Video Folder. If any clip's original isn't there, the batch doesn't start at all -- it says which clips and which folder it looked in, so you can fix the path. (It used to carry on and write only the right eye.) A clip whose original goes missing during the batch fails on its own; the rest carry on. Quick startPick the chapter, check a clip in the preview, choose the Output Format your TV or player wants, and press Start Blending. 1 2 3 4 5 6 7 8 9 1 Folders · 2 the preview · 3 Mask Processing · 4 Shadow · 5 Upcoming Jobs · 6 Film Grain · 7 Current Clip Info · 8 Options · 9 the buttons."
},
{
"part": "The pipeline · 10 Merging",
"title": "1 Folders",
"href": "part2.html#merging",
"text": "A chapter fills in all four: Inpainted Video Folder, Original Video Folder, Mask Folder and Output Folder. Otherwise Import from Inpainting (or Import from Inpainting V2) fills them from the last run. Encoding Options… and the 8-bit / 10-bit presets set how the finished video is saved."
},
{
"part": "The pipeline · 10 Merging",
"title": "2 The preview",
"href": "part2.html#merging",
"text": "The same kind of preview as Splatting's: two views, click to flip between them, with the frame slider, play, the full-screen icon in the picture's top-right corner (Full Preview) and the brightness control. Preview Scale starts at Fit, as Splatting's does: the picture fills the space it has; a percentage, the mouse wheel or a double-click zoom picks a size instead. The views to know: Blended Image: the finished right eye, fills blended in. Processed Mask: where the fills go, after the mask settings below. Inpainted Right Eye, Warped (Right BG) and Original (Left Eye): the ingredients, to compare. The anaglyph, side-by-side and Wigglegram views, to judge the 3D, and Glasses-Free 3D on an SR 3D monitor, as in Splatting. All of them: Appendix D. The preview doesn't show film grain or colour correction; those are only added on export and in the Grain Match Preview."
},
{
"part": "The pipeline · 10 Merging",
"title": "3 Mask Processing Parameters",
"href": "part2.html#merging",
"text": "The mask decides which parts of the right eye come from the fill. Shaping it right hides the joins: Binarize Thresh: turns the mask into hard black and white before the rest. Below 0 switches it off and keeps the soft mask. Dilate Kernel: grows the mask, to cover thin dark lines at the edge of the fills. Blur Kernel: feathers the mask, so the fills fade in instead of ending in a hard edge. Highlight Mask in Preview shows the mask over the picture; Reset to Defaults puts the mask settings back."
},
{
"part": "The pipeline · 10 Merging",
"title": "4 Shadow",
"href": "part2.html#merging",
"text": "A soft shadow cast from the edge of the mask into the fill, which helps the fill sit behind the object in front of it. Open the section to set how far it reaches (Shift), how it fades (Gamma), and how strong it starts and ends."
},
{
"part": "The pipeline · 10 Merging",
"title": "5 Upcoming Jobs",
"href": "part2.html#merging",
"text": "The chapter's clips, as in Splatting: click one to preview it, tick clips and press Process Selected to merge just those, or Move to Finished to set them aside. Right-click a clip for Merge This Clip Now or Move to Finished on that clip alone."
},
{
"part": "The pipeline · 10 Merging",
"title": "6 Film Grain (Inpainted Fills)",
"href": "part2.html#merging",
"text": "The AI's fills are clean, so on film footage they can look smoother than the grain around them. Add matching grain to inpainted fills adds film-style grain only where the fills are, fading with the mask; the original picture is never touched. Auto-match grain per clip on render measures each clip's own grain and sets the amount for it. A clip you've tuned by hand keeps your setting. Grain Match Preview… shows the fills with grain next to the original, with magnified close-ups and the measured grain of each, so you can tune it by eye."
},
{
"part": "The pipeline · 10 Merging",
"title": "7 Current Clip Info",
"href": "part2.html#merging",
"text": "The clip, its size and length, and the borders and flip it carries from Splatting."
},
{
"part": "The pipeline · 10 Merging",
"title": "8 Options",
"href": "part2.html#merging",
"text": "Output Format: the kind of 3D video to make. Full SBS (the default): both eyes side by side at full size. The best quality. Half SBS: both eyes squeezed into one frame; works with more TVs and players. Full SBS Cross-eye, Double SBS, Split L/R Output (two files), Right-Eye Only, and two Anaglyph versions for red/cyan glasses. Enable Color Transfer: matches the fills' colours to the original. Leave it on. Pad to 16:9: adds black bars above and below so wide movies are a standard TV shape, which some 3D players need. Add Borders: applies the edge borders each clip got in Splatting. Resume: finished clips move into finished, so a stopped batch carries on with the rest. Split L/R: also saves each Full SBS video as separate left and right files; Delete SBS Videos then keeps only those. Use GPU for Mask Processing is faster, but it blends a whole chunk of full-size frames on the graphics card at once, and keeps that memory for the rest of the batch: at a large Batch Chunk Size it can fill a 16 GB card, even while the card is idle during encoding. Turn it off, or lower Batch Chunk Size, if the card is short on memory -- most of a Merging run is the encode, so it costs little time. Batch Chunk Size is how many frames are held at once. Erased objects put back. On static shots that don't have any camera movement, Inpainting has to guess what's behind the foreground objects (a lamp post, a railing, a closeup shot through a fence, cage/jail bars) it sometimes can't see enough to invent good detail. Erase in the Depth Map Editor takes the object out first, so the scene behind it is filled in from the frames it can pull from but also generatively fill the holes created from the object removal. With Put back on there, Merging then lays the object back over both eyes at the depth you set, and the gap beside it shows that recovered background instead of a guess. Over, the usual choice, puts the original's pixels back inside the object's outline, for a solid object. Add puts back only what erasing took away, for something you see through. Put Back works in the preview and the batch alike, flipped clips included. Merging on a clip a man was erased from. Its Put Back section, on the left, is on Over with Original Depth, setting him at the depth the original clip's own map gave him; Grow −3 and Blur 10 soften his outline. The preview shows him laid back over the street, in 3D, and the clip in Upcoming Jobs carries the violet Object removed badge. When the clip on screen has one, a Put Back section shows in the left column with its controls, saved with the clip and shown live in the preview: Depth (Add): Glass Plane lays what came off a window -- dust, a reflection -- on the glass itself. A depth map sees through glass, so each window gets a plane fitted to the depth of the car or frame around it, and the layer is warped at that plane, as Splatting warps the scene. Convergence moves it forward (−) or back (+) and Disparity sets how strongly its slope reads. Parallax moves it by one amount instead. Depth (Over): Original Eyes cuts the object from the original clip's own merge -- the conversion made before erasing, the object still in it: its left eye inside the object's mask, and its right eye inside the mask moved to where the object is there (measured between the two eyes). Every pixel of the object is what that conversion rendered, so its edges stay as they were. Choose that merge's folder with Choose…; its depth is that conversion's, so Convergence and Disparity don't apply. Until a merge is found it uses Original Depth, and says so. Original Depth warps the object with the original clip's depth map at that map's own scale. Depth Map puts the object at the depth the original clip's depth map gives it -- the one made before erasing, with the object in it -- pixel by pixel, moved exactly as Splatting moved the scene (the clip's own Disparity, Convergence, Gamma and keyframes). A slanted or bowed thing, like a cage's bars, keeps its shape in 3D. Parallax moves the whole object by one amount. Convergence (Depth Map) moves the whole object off the depth its map gives it, keeping its shape: + sets it back, − brings it out (−2 to 2). Disparity (Depth Map) is how much 3D the object gets, in % of the scene's: 100 as Splatting gave the scene, less flattens it toward the screen, more deepens it. Parallax is the one amount otherwise. Grow widens the object's mask (+, to take in more of its edge) or narrows it (−, to leave a halo of old background out); Blur softens its edge; Opacity is how much of it comes back. Depth Map needs the clean clip's depth map in Splatting's depth folder (where Splatting read it) and an erase made by this version of Studio, which keeps the original depth map for it. When the clean map isn't there, the section says so in yellow: the object then uses Parallax, and Convergence and Disparity do nothing until Splatting's Depth Maps points at it."
},
{
"part": "The pipeline · 10 Merging",
"title": "9 The buttons",
"href": "part2.html#merging",
"text": "Start Blending merges every clip in the folders. Process Current Clip merges only the clip in the preview, to test your settings first. Stop stops once the piece being worked on is finished. Move to Finish moves the clip in the preview into finished and goes to the next. Fill Edges opens Extra > Edge Fill on this stage's folders, to refill the strip a deep 3D shift leaves at the frame's edge -- see 11. Merge All SBS to 1 Video puts the whole movie back together -- see 10. View Output Folder opens the finished videos."
},
{
"part": "The pipeline · 10 Merging",
"title": "10 Merge All: the whole movie",
"href": "part2.html#merge-all",
"text": "Once the scenes are merged, Merge All SBS to 1 Video joins them into one video: every chapter in order, and every scene in the order it plays. The scenes are the clips Scene Cut made, found in each chapter of the Source folder; each one's merged output is looked for in that chapter of the merged folder, subfolders included. Split _L / _R files are joined side by side. For a TV show it's the episode Merging's chapter belongs to. When every scene has its merged output, it asks you to confirm and names the file it will write. When some don't, it lists them, each with the stage it still needs: Play opens the scene's clip in your media player, to see which scene it is, and Send to loads it into that stage with the chapter's folders. Merge Anyway is for scenes you left out on purpose: each missing scene becomes black, for as many frames as it has, so the sound still lines up. It runs like any Merging job: the bar shows it, Stop ends it and discards the unfinished file. How it's written is under Encoding Options… › Merge All: Layout (Full SBS, Half SBS, or L and R files, a whole-movie file per eye), the codec and quality, the container, and the Movie (sound) -- the movie file whose soundtrack goes in, every audio track of it (use MKV for TrueHD / Atmos). Leave that empty for no sound. The file goes in the project's folder, as <project> - 3D Full SBS.mkv, unless you set an Output Folder. Merged in DNxHR?When every scene is in one frame-by-frame format (DNxHR, ProRes) the scenes are joined as they are, which is quickest. Mixed formats or H.264 / H.265 clips are decoded scene by scene first: slower, but the result is the same."
},
{
"part": "The pipeline · 10 Merging",
"title": "11 Edge Fill: the strip at the frame's edge",
"href": "part2.html#fill-edges",
"text": "With a lot of depth, the 3D shift uncovers a strip at the frame's edge that nothing in the picture can fill -- usually on the right eye's left side. Inpainting guesses it from a thin margin, and the guess shows as a dark bar or the edge smeared across. Extra > Edge Fill refills that strip with ROSE, on merged clips. Merging's Fill Edges button opens it on Merging's Output Folder and Mask Folder. What it suitsEdge Fill does best on simple backgrounds with little movement. Walls, sky and plain objects -- a house, a bench, a cabinet -- carry on into the strip well. It can't rebuild fine detail: a face or text in the strip comes out rough, so leave those clips unticked. Edge Fill on five merged clips from one chapter. Each still is the right eye at the frame where its strip is widest, with a red line where the strip ends and its width under it -- 26 or 27 px here, at the right eye's left edge. Two clips have no strip there, so only three are ticked; Fill 3 Clips refills them with ROSE. On the left: Merged Folder, the merged clips (Full SBS, or _L / _R files), and Mask Folder, their splats (or its finished), which say how wide each strip is. In a project, the chapter dropdown above them points both at a chapter -- its Merged folder and its splats -- as the stages' dropdowns do; editing a folder by hand switches back to Manual paths, and the pick is kept per project. A project Edge Fill hasn't been on yet takes Merging's chapter or folders. Then the fill's settings. On the right, the clips as a grid of stills, the ones it can fill ticked. Each still is the eye with the strip, at the frame where it's widest, with a red line where the strip ends and its width under it -- so you can see which clips have a dark bar. A clip with no strip, or one already filled, is left unticked. Select All ticks every clip with a strip (Deselect All unticks them). Click a still to tick or untick it; right-click it for Fill Just This Clip, or to Play the clip in your default player -- the eye with the bar first -- and watch the whole thing. The Thumbnails slider (or its − / +) makes the stills bigger or smaller, fewer or more to a row. Each clip's strip is measured once and remembered; it is measured again only when the clip's splat mask changes. Refresh looks through the folders again. While another stage's job runs the grid waits -- looking means reading every mask -- and looks once that job ends. A filled clip's tile is light green, as the clip lists show a finished clip -- one filled in this run as soon as it's done. Open Console, under the fill's settings, shows each clip's fill as it goes, with its progress bars, on the console's Extra tab. It works during a fill too. To fill one clip from Merging's Upcoming Jobs, right-click it there and pick Fill Edges: its merged clip is filled straight away (once it has been merged). Steps is how many steps ROSE takes -- type any number. 6 looks the same as 50 on an edge strip; fewer is quicker but softens fine detail. Resolution is how big ROSE works the edge: 560 keeps more texture (a wall's ridges, grain) and is quicker; 784 carries lines that run into the edge -- a roofline, a railing -- on straighter, a little softer and about twice as long. Native works the edge at the clip's own size, in overlapping tiles near ROSE's own size faded into each other: the sharpest and closest to the real picture (a wall's ridges and rivets, straight lines), about 2–2.5× 560's time. With 784 and Native the fill's colour is matched to the picture beside the strip (ROSE drifts a few levels at those sizes). Right Edge Too: Splatting leaves the strip on the right eye's left edge, so by default that is the only edge Edge Fill measures and fills. Tick it to look at the right eye's right edge as well, for a clip whose strip really is on that side; the clips are measured again when you change it. Match Grain gives the fill the film grain of the picture beside it, so the strip isn't smoother than the rest of the frame. Match Detail sharpens the fill to the picture's own fine detail: ROSE works the edge smaller than the frame, so its fill comes back softer. How much is measured on each clip, where ROSE repainted real picture just past the strip, and nothing is sharpened when the fill is already as sharp. The fill follows the splat mask row by row, frame by frame -- each row is filled only as far as its hole reached, as Inpainting's Hi-Res Blend does -- so the real picture next to a narrower hole is kept. Each clip's files are written again in their own format (DNxHR stays DNxHR; H.264 / H.265 with Merging's encoder settings) with only the strip changed; the originals go to the Recycle Bin (on a drive without one, to edge_fill_originals, a second fill's under a numbered name). A clip with a file open in another program -- a player -- isn't touched: close it and fill again. A strip narrower than about 12 px (at 1920 wide) is left to Inpainting's own fill. Fill runs the ticked clips as a job of its own: the bar shows it, Stop leaves the clip being filled as it was -- all its files: they're only swapped in once every one is written -- and the grid looks again when it ends. A short scene takes about half a minute, most of it ROSE starting. The console's Extra tab follows it: each clip's strip, ROSE's size and steps, the grain and detail, the files written, a bar for each stretch (reading, each ROSE window's steps with their s/it, blend and write) and the time it took. Needs ROSEEdge Fill uses the same ROSE as the Depth Map Editor's Erase: set it up there once (Set Up ROSE)."
},
{
"part": "The pipeline · 10 Merging",
"title": "In Settings",
"href": "part2.html#fill-edges",
"text": "Save Preview Frame and Save Preview as SBS save the frame on screen as a picture. There's also Load / Save Settings, Reset to Default, Restore Finished, Enable Debug Logging and a User Guide."
},
{
"part": "The pipeline · 10 Merging",
"title": "When something isn't right",
"href": "part2.html#fill-edges",
"text": "Thin dark lines around the fillsRaise Dilate Kernel. Hard edges where the fills startRaise Blur Kernel. Fills look smoother than the filmTurn on the film grain, and check it in the Grain Match Preview…. Fills a different colourTurn on Enable Color Transfer. The player shows it squashed or stretchedTry Half SBS, or Pad to 16:9. Out of memory, or the VRAM meter full during a mergeTurn off Use GPU for Mask Processing, or lower Batch Chunk Size. The batch won't start: no originalThe Original Video Folder is wrong, or the clips it names aren't in it. Fix the path and start again."
},
{
"part": "The Depth Map Editor",
"title": "11 When to fix a depth map",
"href": "part3.html#when",
"text": "DepthCrafter gets most shots right, but sometimes an object comes out wrong: a sign that should be flat bulges, a person merges into the wall behind them, a road that should run into the distance is flat, or something is missing from the depth altogether. In 3D that object then sits at the wrong depth, or bends. You'll usually notice it in Splatting's preview. From there, the round trip is: In Splatting, with the clip in the preview, press Edit Depth Map. The editor opens on the Extra tab with that clip and its depth map loaded. Select the object, track it through the clip, and change its depth (sections 13 and 14). Press Render Edited Depth. Studio asks whether to use it in Splatting: choose Use in Splatting. You're back in Splatting, on the same clip, now reading the edited map. Its clip in Upcoming Jobs gets a pencil badge. 1 2 3 4 5 6 1 Clip · 2 View and Tool · 3 the picture · 4 frames · 5 Layers and tracking · 6 the pages: Segment, Layer, Gradient, Global, Output."
},
{
"part": "The Depth Map Editor · 11 When to fix a depth map",
"title": "Opening a clip yourself",
"href": "part3.html#when",
"text": "In the Clip panel, pick the Source video (the colour clip) and the Depth map (filled in for you when <clip>_depth.mp4 is found beside it), then press Load Clip. Pick From Last DepthCrafter Job lists the clips DepthCrafter made last. If you've edited a clip before, your edit opens again with it."
},
{
"part": "The Depth Map Editor · 11 When to fix a depth map",
"title": "Looking at the clip",
"href": "part3.html#when",
"text": "View: RGB (the colour clip), Depth (the original map), Edited (the map with your changes) and 3D (section 15). Masks tints each object you've selected in its layer's colour; Outline only draws just its edge. Colour depth shows depth in colour instead of grey, where small steps are much easier to see; it uses the map's full 10 bits, so even one level of difference changes the colour. Dither (on by default) shows the grey in the same detail: the map has about 900 shades but the screen only 256, so a gentle slope like a wall or floor would show as flat bands; a faint fixed pattern brings back the shades in between. Only the picture changes: the rendered map is always the full 10 bits. RGB/D (RGB over depth) lays the colour clip over the depth, to check that edges line up. Specks tints the bright specks DepthCrafter leaves on the left edges of things -- see Taking out left-edge specks, below. Step through frames with the slider, the < > buttons, or the arrow keys. Zoom in with the mouse wheel, about the point under the pointer, as in Splatting and Merging; drag with the middle button to move around. The Zoom box after the frame counter picks a size, or Fit for the whole frame again. In the 3D view's Right Eye + Holes, which takes no clicks, the left button drags too and a double-click zooms in, or back to Fit. The same frame as Depth, with Colour depth on."
},
{
"part": "The Depth Map Editor",
"title": "12 Setting up SAM 3",
"href": "part3.html#sam3",
"text": "SAM 3 is the AI that finds and follows objects. It isn't installed with Studio, because its model file is large and its makers ask you to request access first. Set it up once, from the Segment page: Packages. Press Install Packages. It shows what it will add before doing anything, and never removes anything. Access. SAM 3's model is gated: request access on its Hugging Face page, huggingface.co/facebook/sam3, then sign in to Hugging Face in Settings → Studio → Models (or run hf auth login in a terminal). The model. Press Download Weights. About 3.4 GB lands in weights/SAM3, with a bar showing how far it has got. Stop ends it at once; what came down is kept, and the next Download Weights carries on from there. It's the same download as SAM 3's row in Settings → Studio → Models. Before SAM 3 is set up, the Segment page opens with its SAM 3 panel: what's still needed, and Install Packages / Download Weights. Here the packages are in and only the weights are missing. Once both are done the panel goes away."
},
{
"part": "The Depth Map Editor · 12 Setting up SAM 3",
"title": "Graphics memory",
"href": "part3.html#sam3",
"text": "SAM 3 needs about 4 GB of graphics memory. It loads on your first click and stays loaded; Unload Model frees it straight away, and Studio frees it by itself when another stage starts a job. Keep video frames in system RAM saves graphics memory on long clips (each frame otherwise takes about 6 MB on the card), at some cost in speed. The same three things are in Settings (with the Depth Map Editor showing, under Extra → SAM 3), along with Uninstall SAM 3…. It moves the model file (several GB) to the Recycle Bin, so you can restore it from there; the small packages stay installed. To use SAM 3 again, press Download Weights."
},
{
"part": "The Depth Map Editor",
"title": "13 Masking objects",
"href": "part3.html#masking",
"text": "Every change you make lives in a layer. A layer is one selection (one or more objects, its mask) plus the depth change applied inside it. Layers apply from the bottom of the list up, so a layer sees the changes of the layers below it."
},
{
"part": "The Depth Map Editor · 13 Masking objects",
"title": "Selecting an object",
"href": "part3.html#masking",
"text": "On the Segment page, pick a Tool above the picture: Add: click the object. Click again elsewhere on it to add to the selection; Ctrl+click starts a second object in the same layer. Remove: click an area to cut it out of the mask. Right-click removes with any tool. Box: drag a box around the object. Find: type what it is, such as person, car or road, and press Find. SAM 3 selects every one on the frame, and while tracking it also picks up new ones that come into the shot. Shape: draw the mask yourself, for a static camera shot -- it holds on every frame and nothing is tracked. See Drawing a mask with Shape below. Clicking with no layer selected starts a new one; so does New. Segment on chooses which picture SAM 3 looks at: RGB (the usual, and the only way to catch an object the depth map missed) or Depth (for shapes that are clear in depth but busy in colour). A click that went wrong can come out again: click its dot on the picture to pick it (a yellow ring shows it), Shift+click to pick more, and press Delete. SAM 3 works the mask out again from the clicks that are left; delete a frame's last clicks and its mask goes with them. Undo puts them back, and Esc lets go of the pick. A click on a dot only picks it -- it adds no new click. Three clicks with Add (the green dots: face, shirt, jacket) and SAM 3 has the whole person: the mask is tinted over the picture, and Layer 1 is in the Layers list, not tracked yet."
},
{
"part": "The Depth Map Editor · 13 Masking objects",
"title": "Drawing a mask with Shape",
"href": "part3.html#shapes",
"text": "Use Shape on static camera shots -- a locked-off camera on a tripod, or one that rides with what it films (a car's interior seen from the dashboard). Anything that holds still on screen there sits in the same place on every frame, so there's nothing to track: you draw its outline once and the layer uses it on every frame. SAM 3 doesn't run at all, so it's instant and never drifts or flickers the way a tracked mask can on something hard to follow -- a window, a wall, a sign, a doorway, a TV screen. If the camera moves (a pan, a push-in, a shake), the shape stays put while the picture moves under it; track those with SAM 3 instead. Pick Shape in the Tool row; a row under it chooses the kind: Points: click each corner of any outline. A line from the last point follows the pointer. Close it by clicking the first point again, double-clicking, or pressing Enter; it needs three points or more. Right-click or Backspace takes the last point back, and Esc drops the shape. Rectangle and Ellipse: drag one out. Hold Ctrl as you start a shape to cut its area out of the layer instead (it draws red while you place it, yellow when it adds) -- a window frame's centre bar, a hole in a sign. Shapes apply in the order they were drawn, so a cut takes out what was drawn before it. A layer can hold any number of shapes; Clear Shapes takes them all off it (Undo brings them back, and every shape is its own Undo step). Changing the tool, the kind or the layer drops a shape you've started but not closed. To lengthen or trim one side of a shape, drag that side. Point at an edge of a Points or Rectangle shape on the selected layer (the pointer turns into a double arrow) and drag it: an upright edge moves left or right, a flat one up or down, and the rest of the shape stays where it is -- drag the end of an L-shaped outline outwards and that arm gets longer. A slanted edge moves whichever way it's closer to. Each drag is one Undo step. A shape goes on the selected layer when it's a shape layer or still empty. With a tracked layer selected, the shape starts a new layer of its own (Shape 1, Shape 2...), so tracking never wipes a shape and a shape is never tracked. Draw in the 2D views (zoom in with the wheel for a close outline; the middle button pans). From there a shape layer works like any other: Alter, Add or Erase on the Layer page, its edge and depth settings, keyframes, and its frame range if the change should only apply to part of the clip. The shapes are saved with the project as outlines, not pictures, so they stay sharp at any size -- after Upscale Map and in a render at the source's resolution. For dust or reflections on glass, draw the windows with Shape and use Erase from Clip's Dust or Static (below)."
},
{
"part": "The Depth Map Editor · 13 Masking objects",
"title": "Tracking it through the clip",
"href": "part3.html#shapes",
"text": "A selection starts on one frame. Press Start Tracking and SAM 3 follows the layer's objects through every frame, forwards and backwards from where you selected them. The edit only applies to the whole clip once the layer is tracked. Start Here, beside it, tracks from the frame you're on in one direction only, and leaves every other frame's mask as it is. The arrow next to it picks the direction: on over the next frames (this one included), or back over the previous ones. Use it when the start of a shot is already right and only the rest needs tracking again, or the other way round. The thin line under the buttons shows how far tracking has got. If the mask driftsGo to a frame where it's wrong, fix it there with Add or Remove clicks, and track again -- with Start Here, only from that frame on. SAM 3 uses every frame you've corrected, not just the first. Patches. On a layer that's already tracked, an Add click or a Box outside its mask -- a piece the tracking missed -- is tracked on its own and added to the layer's masks. The layer's own tracking, a text search's included, isn't redone around it. Frame 9 Frame 91 After Start Tracking, the mask clicked on frame 50 follows him through the clip, as he walks over the bridge and the camera pans with him."
},
{
"part": "The Depth Map Editor · 13 Masking objects",
"title": "Managing layers",
"href": "part3.html#shapes",
"text": "Up and Down change the order layers apply in; Delete removes one. On the Layer page, On switches a layer's edit off without losing it, and Name labels it. Clear Layer Prompts forgets a layer's clicks, boxes and text and its mask, but keeps its depth settings. Undo and Redo, the two arrows after the frame counter (or Ctrl+Z and Ctrl+Y), step back and forth through your edits: clicks and boxes, tracking, new and deleted layers, every dial, keyframes, gradients and the whole-frame settings. Hover over an arrow to see which edit it will undo or redo. A whole drag of a dial counts as one step, and so does typing a layer's name. They wait while a job such as tracking is running, and Load Clip or Upscale Map starts the history afresh. While you're typing in a box, Ctrl+Z undoes the typing instead."
},
{
"part": "The Depth Map Editor",
"title": "14 Changing depth inside a mask",
"href": "part3.html#changing",
"text": "With a layer selected, the Layer page sets what happens to the depth inside its mask. Watch the Edited view (or 3D) as you go. Dials and pixelsEvery setting is a dial: drag it, scroll on it, or use the arrow keys, and right-click or double-click to reset it. Top, Bottom, Left and Right edge, Grow, Feather and Smooth count pixels of your video, the same pixels Splatting's Dilate settings use, and move in half-pixel steps. (The depth map is usually smaller than the video; Studio works out the difference for you.)"
},
{
"part": "The Depth Map Editor · 14 Changing depth inside a mask",
"title": "Mask Edge",
"href": "part3.html#changing",
"text": "Edge snap: pulls the mask's edge onto the depth map's own edges, so the change lands exactly where the depth changes. Grow: widens (+) or narrows (−) the area the change applies to, to cover a halo of wrong depth or keep off the background. Feather fades the change in at the edge; Fill holes in the mask fills gaps SAM 3 left, such as between an arm and a body. Close gaps: seals tears and notches up to about twice this wide where they reach the mask's edge -- gaps Fill holes can't close, since it only fills holes the mask surrounds. The outline elsewhere stays where it is. Steady: evens out a tracked mask over this many frames either side. A pixel stays in the mask when at least half the frames around it have it, so an outline that SAM 3 draws a little differently every frame -- the foot of a post with no strong edge to hold on to -- stops shimmering in 3D. Best on a locked-off shot or a slow object; on fast movement keep it low, or the mask trails a little."
},
{
"part": "The Depth Map Editor · 14 Changing depth inside a mask",
"title": "Depth Inside the Mask",
"href": "part3.html#changing",
"text": "First choose what the mask is for, with the Mask switch at the top: Alter (the usual): the mask picks out an object, and the settings below change the depth that's already there. Add: the mask is the object. Use it when the depth map missed something, or made it a blob, like a mannequin that came out as a few polygons. Inside the mask, the depth is replaced by Depth; Sample takes it from the object itself, the nearest part of what the depth map has there. Push / pull and the gradient still work on top, to give the shape some roundness. Edge snap is off in Add, since the mask's outline is the point; segment on the RGB so the outline is the real one. The rest of Alter's settings work in Add too: Top edge, Bottom edge, Left edge and Right edge move that edge of the shape, and Smooth, Contrast and Flatten shape a gradient's depth (a shape without one is flat, so they have nothing to change). Erase: the object is taken out. Its depth is filled from the depth around it, and Erase from Clip takes it out of the video itself -- see Erasing an object, below. In Alter, the settings are: Despeck (with its Strength): the bright specks DepthCrafter leaves just inside the left edges in the mask come down to the object's own depth -- see Taking out left-edge specks, below. Off until you turn it on. Brightness: moves the object nearer (+) or further (−). Contrast: stretches or squashes the depth differences inside the object. 0× makes it perfectly flat. Flatten: pulls the object toward one depth without moving it. Good for screens, signs and walls. Smooth: blurs the depth inside the object only, removing noise without softening its edge. Opacity: how strongly the whole change applies. Top edge, Bottom edge, Left edge and Right edge move only that edge of the object's depth; the opposite edge stays put. Negative pulls the edge in (the depth behind shows there), positive pushes it out. A negative Top edge fixes, for example, a beam that Splatting's Dilate Y bent upward; Left and Right do the same sideways, for depth that spills past an object on one side."
},
{
"part": "The Depth Map Editor · 14 Changing depth inside a mask",
"title": "Example: vertical warping",
"href": "part3.html#changing",
"text": "Splatting only ever shifts pixels sideways, but its Dilate Y spreads a near object's depth up and down into whatever is behind it. Here the railing's depth has spread into the wall, so the wall's slats shift with the railing and bend around it. The fix is in the depth map: select the railing, track it, and pull its depth edges back in with a negative Top edge and Bottom edge, so the wall keeps its own depth. The slats then come out straight. In a static shot, where the camera and the railing stay put, draw the railing with the Shape tool instead (Drawing a mask with Shape): one mask covers the whole clip, with no SAM 3 and nothing to track. Before After The same frame of the right eye, before and after fixing the railing's depth."
},
{
"part": "The Depth Map Editor · 14 Changing depth inside a mask",
"title": "Frames",
"href": "part3.html#changing",
"text": "Limit a layer to part of the clip: In starts it at the current frame, Out ends it there, All uses every frame."
},
{
"part": "The Depth Map Editor · 14 Changing depth inside a mask",
"title": "Keyframes",
"href": "part3.html#changing",
"text": "A layer's settings can change over the clip, as in Splatting. On a frame, set the layer's sliders and press S for a static key (the values hold from here until the next key) or D for a dynamic key (the values slide smoothly to these from the key before) -- a mannequin's depth that comes nearer as the camera passes it, say. The keys show on the frame slider, blue static and orange dynamic, and the sliders follow them as you scrub. On a key, moving a slider changes that key; between keys, moving one adds a dynamic key there, so the change shows straight away (as in Splatting). A gradient's Sample From Object does the same. ‹ and › jump between keys, Delete removes the one on this frame and Clear removes them all. The number settings are keyed (Depth, Push / pull, Contrast, Flatten, Smooth, the edge moves, Grow, Edge snap, Feather, Opacity and the gradient's ends, curve and detail); the choices -- Alter / Add / Erase, Fill holes, the gradient's shape -- and the frame range stay the layer's own. Keys are saved with the project. The ∿ button beside the key buttons opens the Shaper for the dynamic key the playhead is heading for: how the values travel into it -- a steady ramp, an ease, an LFO swinging between the two keys' looks, or your own points and bends. It works as in Splatting (Part 2, 8d). The Layer page with Push / pull at +30%, masks hidden: in the Edited view he is lighter than the map around him, so nearer."
},
{
"part": "The Depth Map Editor · 14 Changing depth inside a mask",
"title": "Gradients, for things that recede",
"href": "part3.html#changing",
"text": "A road, a floor, a long wall or a table seen at an angle should run from near to far, but depth models often flatten them. On the Gradient page, tick Paint a depth gradient inside the mask, pick the Gradient tool, and drag on the picture from the object's near end to its far end. Shape: Linear along your line, or Radial outward from its near end, for round objects. Blend: Replace makes the ramp the new depth; Only nearer and Only further can only move pixels one way; Add tilts the existing depth. Near, Far and Curve set the ramp; Detail keeps some of the object's own surface on top of it. Sample From Object fills Near and Far from the object itself, a good start. Follow the object as it moves pins the ramp to the object, so it moves and scales with it. A cobbled street clicked as its own layer, then a gradient dragged with the Gradient tool from N (near, at the bottom) to F (far, where the street meets the railings). The Edited view runs from light to dark along it; Outline only shows the mask's edge."
},
{
"part": "The Depth Map Editor · 14 Changing depth inside a mask",
"title": "Taking out left-edge specks",
"href": "part3.html#despeck",
"text": "Now and then DepthCrafter leaves a bright speck, or a soft bright glow, just inside the left edge of an object -- the tip of a horn, a shoulder, the corner of a chair -- most often when nothing nearer is in front of it. In 3D it pokes out of the object into the background as a spike. To take them out: Turn on Specks beside the View. Every speck on the frame is tinted by how far it would come down: cyan where no layer with Despeck on covers it yet, orange where one does. Make a layer for the object with the cyan speck (or use the one you have), track it, and in Alter turn on Despeck. The speck turns orange and, in the Edited view, comes down to the object's depth. Step through the clip to check it holds. It goes first, so the rest of Alter -- Brightness, Contrast and so on -- works on the cleaned depth. There is no level to pick: on every frame, each speck is lowered to the depth of the object just beside it, so a glow that fades in and out, or an object that moves, needs nothing more than the tracked mask. Only the left edges inside the mask change. Strength is how far they come down (100% all the way). A thing whose left side really is nearer, like a face turned three-quarters, can lose a little there -- lower Strength, or keep the layer to the frames that need it. Turn Despeck off and on again to compare; off, its specks show cyan under Specks. A small thing with a white dot inside a grey body -- a floating particle -- has nothing beside it for Despeck to go by. Mask the particle snugly and turn on Level to edge: anything in the mask brighter than the grey around its outer edge comes down to that grey, on every frame. A particle that really is near is white all the way to its edge, so it stays as it is. Strength works on it too. Before After A speck DepthCrafter left just inside a figure's left edge (the arrow): a bright patch, nearer than the figure itself, that would poke out of it in 3D. After Despeck it is down at the figure's own depth, and the rest of the figure is as it was."
},
{
"part": "The Depth Map Editor · 14 Changing depth inside a mask",
"title": "Erasing an object",
"href": "part3.html#erasing",
"text": "Some things are better out of the shot than at the wrong depth: a sign, a pole, a logo, a mark on the lens, or reflections on glass. Make a layer for them, set its Mask switch to Erase, and track it through the clip. In the depth map, the object's depth is then filled from the depth around it. To take it out of the video as well, use the Erase panel on the Layer page: A man tracked in one layer, its Mask switch on Erase. The Erase panel is under it: Erase from Clip, the Method (ROSE), and ROSE's settings, with Pre-fill on Auto and Refine at full size ticked. Method: ROSE takes an object out, along with its shadow, reflection or glow, and fills its place with an AI video model. Static takes off reflections that hold still on glass in a locked-off shot -- a TV screen or a shop window, where the camera and the lights don't move but what's seen through the glass does. Static needs no model and no graphics card, and takes seconds. Dust takes dust and specks off glass the camera rides with -- a windshield seen from inside a car -- where they hold still on screen while the world moves behind. Each frame's own dust comes off, however the light on it changes, and Merging's Put Back lays it back on the glass (Add, Glass Plane). Draw the windows with the Shape tool; Size px is the widest speck taken (9 for a 1920-wide clip). It reads the clip twice and uses the graphics card briefly. With Dust or Static the Erase layers leave the depth map as it is -- the glass is seen through, so the depth behind it stays; only ROSE fills the depth where its object was. Erase from Clip takes every enabled Erase layer's object out of the clip. Only the frames with something to erase are processed, and only the area inside the mask changes; every other pixel stays the original. The clip is replaced where it is, so Splatting and DepthCrafter use the erased one; the original and the erase mask are kept in edit_history beside it. With ROSE it's slow, minutes per 81 frames; Stop ends it. ROSE's settings: Steps (fewer is faster and softer; 50 is ROSE's own, 20 to 30 is enough to judge a mask), Seed (another seed gives another fill to compare), Grow px (how far past the object it repaints -- a little takes its soft edge, more reaches a shadow the mask missed), Feather px (how softly the result blends in), Size (480p is what ROSE was trained at; larger is sharper but slower and needs more memory) and Window (frames per pass: 81 keeps the fill steadiest, shorter needs less memory -- a shorter clip runs at its own length). Static has one, Size px: the widest reflection shape it takes. Pre-fill: ROSE normally sees the object under its mask, which is how it takes the object's shadow and reflection along. A big object -- a person filling a close-up -- can come back redrawn instead of removed. Pre-fill blends the object out from around it first, so ROSE invents what's behind it. Auto turns it on when the object covers 10% of the frame or more. Refine at full size runs ROSE a second time at the clip's own size, in tiles (3×2 at 1080p, only those with something to erase), over the first pass: sharper, with the colour kept from the first pass. It takes about as long again, or longer. Before After Erase from Clip on that layer: the man taken out of a 120-frame street shot with ROSE at 720p, 50 steps, Pre-fill on Auto (it stays off: he covers only 9% of the frame) and Refine at full size. ROSE fills in the street behind him from what it sees around him and in the other frames -- the lamp post he walked in front of is its guess too, so check a fill like this in 3D before you use it. Setting up ROSE. ROSE runs in its own environment, in weights/ROSE, because it needs older libraries than Studio. Until it's there, the panel offers Set Up ROSE, which fetches ROSE and about 3.7 GB of model files (it needs git and uv). Static doesn't need it. Putting it back at its own depth. Once a clip is erased, Put back lays what came off back into the finished 3D, at a depth of its own, in Merging: Add adds back what erasing took away -- for glass, reflections, rain, smoke: anything seen through. Over lays the original's pixels inside the object's own outline -- for a solid object. The parallax, in % of the picture's width, sets where it sits: 0 is the screen, + in front of it, − behind it. Sample sets the parallax from the depth under the mask on every frame, so it follows the surface as the camera or the object moves. The factor beside it scales that depth: ×1.0 puts it on the surface (a mark, dirt, rain on the glass); ×0.5 puts it about twice as far, where a mirror image sits (a reflection in a screen or window). Press Sample again after changing it. Typing a parallax uses that one on every frame instead. Erasing keeps the clip's depth map as it was -- the object in it -- so Merging's Put Back can use Depth Map: the object at its own depth, pixel by pixel. Run DepthCrafter on the erased clip as usual afterwards; the kept copy is in the clip's edit_history folder."
},
{
"part": "The Depth Map Editor · 14 Changing depth inside a mask",
"title": "The whole frame",
"href": "part3.html#erasing",
"text": "The Global page changes the entire depth map before any layer: Invert (for maps where dark means near), Levels, Gamma, Contrast and Brightness. Despeck the whole frame (with its Strength) is Alter's Despeck on every left edge in the frame, no layer needed; it goes first, before Invert and the rest. Check the whole shot with Specks on: something whose left side really is nearer can lose a little -- lower Strength, or use Despeck in a layer for just that object. Its dials: Threshold (how far a spot must stand above its object to be taken -- each spot judged whole), Reach (how far in from an edge it works), Edge (how sharp a drop counts as an edge -- raise it to leave the steps inside an object alone), and Left / Right / Top / Bottom, the box it works in (drawn on the picture while this page is open): keep it to where the specks are, away from things that are fine. Reset Whole Frame undoes them. Local Match (locked-off shots) is for a shot where the camera doesn't move but DepthCrafter changed its mind part-way about how far one part of it is -- a hedge grey at the start and black at the end, which slowly pushes in or out in 3D. Scrub to a frame that looks the way the whole shot should, press Use This Frame, and every frame's depth is matched to it area by area, its fine detail kept. Whatever moves is left as it is -- outlines that shifted get no glow, and a frame where something comes right up to the lens is skipped -- and Strength below 100% only lessens the drift. It goes first of all. Don't use it on pans, dollies or zooms: there everything changes, and it would drag the picture back toward the frame you picked."
},
{
"part": "The Depth Map Editor",
"title": "15 Rendering and edit history",
"href": "part3.html#rendering",
"text": ""
},
{
"part": "The Depth Map Editor · 15 Rendering and edit history",
"title": "Checking it in 3D first",
"href": "part3.html#rendering",
"text": "Before you render, switch View to 3D. It shows the edited depth map through Splatting's own preview, with the clip's own settings, sidecar and effects, exactly as Splatting will make it. Choose Anaglyph for red/cyan glasses, or Right Eye + Holes to see the gaps your change opens. With a glasses-free 3D monitor connected (see Splatting's preview), Glasses-Free 3D shows the picture in 3D on it. A clip that isn't in Splatting's folders -- a single edit, or a depth map from another program -- is shown too: with Splatting's settings as they are on its page (or the clip's sidecar beside the map, if it has one). Splatting goes back to its own clips, where it was, when you open it again. You can keep editing in the 3D view. In Anaglyph and Glasses-Free 3D the tools work as in the other views -- clicks, boxes, the gradient, shapes -- and the layers' masks and shapes are drawn on the left eye only, so through glasses you see them on one eye while the 3D stays clean. The left eye is where the frame is, so the masks line up there; the right eye is shifted by the depth. Untick Masks to judge the 3D without them. Right Eye + Holes shows no left eye, so it's for looking only. Each change redraws the 3D view, which takes a moment longer than the flat views. The 3D view, Anaglyph: the frame through Splatting's own preview with the edit in, before anything is rendered. Red/cyan glasses show him nearer than the railings and houses behind him."
},
{
"part": "The Depth Map Editor · 15 Rendering and edit history",
"title": "Rendering",
"href": "part3.html#rendering",
"text": "On the Output page, press Render Edited Depth. The new map is saved in exactly the same format DepthCrafter uses, so every pixel your layers didn't touch stays exactly as it was. Save Project saves your layers without rendering; rendering saves them too, and they reopen with the clip next time. Render at source resolution (on by default) writes the edited map at the source video's size, 1920 wide for a 1080p clip, instead of the depth map's. RGB layers then use SAM 3's full-size masks, so an object's real outline reaches Splatting at full detail, the way Detect Text's masks do, instead of being squeezed onto the depth map's much coarser pixels. The file is bigger and the render takes a little longer; Splatting runs as fast as before. A layer tracked before this option existed has no full-size masks yet: press Start Tracking once more to make them. The RGB view is shown at up to 720p, finer than the depth map, so you can see what SAM 3 is outlining; with Render at source resolution on, the mask drawn on it is the full-size one Render uses. A clip too long to hold at 720p (over about a thousand frames of 1080p) is shown at the depth map's size instead. The Depth and Edited views stay at the depth map's size. Upscale Map goes one step further and lets you edit at the source's resolution too -- see the next section."
},
{
"part": "The Depth Map Editor · 15 Rendering and edit history",
"title": "Upscale Map: editing at the source's resolution",
"href": "part3.html#upscale",
"text": "DepthCrafter makes its depth maps much smaller than the video -- 768x448 is typical, for a 1920x1080 clip. Each pixel of the map then covers about 2.5 x 2.5 pixels of the picture, and a clip opens in the editor at the map's own size. Every edit works in those coarse pixels: a mask's edge can only sit on the map's grid, and Grow, Feather and the edge dials move it in steps of about 2.5 picture pixels at a time. That's fine for big, simple objects. It isn't for thin or detailed ones -- fingers, hair, a railing, a pole, the gap under an arm -- which fall between the map's pixels and come out blocky or smeared into what's behind them. Upscale Map, beside Load Clip, reads the loaded clip's depth map again, enlarged to the source video's resolution (1920x1080 for a 1080p clip). From then on every edit works in the clip's own pixels: Masks follow the object's real outline: RGB layers take SAM 3's full-size masks, which it already makes from the picture, instead of their copy squeezed onto the map's grid. The dials get finer. Grow, Feather and the edge dials step one picture pixel at a time, so you can tighten an edge by a single pixel instead of two or three. What you see is what renders. The Depth and Edited views show the map at the size it will be written, so there's no surprise when the render is enlarged afterwards. Nothing is invented by upscaling: the map is enlarged the same way Splatting enlarges every depth map to the video's size before it splats (smoothly, bilinear), so anything you don't edit splats exactly as it did. What you gain is room to make your edits fine -- the extra resolution is filled by your masks and dials, not by guesswork. How to use it: Load the clip with its Source video -- Upscale Map needs it, both for its size and for SAM 3's full-size masks. Without one the button stays greyed. Press Upscale Map. The status line counts the frames as they're read; the button then reads Map Upscaled, and the status line says the new size and the old one. Layers you already made come along. RGB layers switch to SAM 3's full-size masks; others (Depth layers, a layer tracked before full-size masks existed) are enlarged, and the status line names them -- press Start Tracking on those once more for SAM 3's full-detail outline. Shapes are drawn again at the new size, so they stay sharp. Check the pixel dials of layers made before upscaling. They keep their numbers, but the numbers now count picture pixels instead of map pixels: a Grow of 6 that reached about 15 picture pixels at 768x448 now reaches 6. Raise them to taste -- usually it's the chance to set them tighter than you could before. Edit, then Render Edited Depth as usual. The map is written at the source's size; Splatting runs as fast as before. Use it when the object's outline matters: a person against a busy background, anything thin or see-through, an edge that shows as a stair-step or a halo in Splatting's preview. For a broad depth change (pushing a wall back, the whole frame's gamma) the map's own size is plenty, and quicker. What it costs. The whole map is held in memory, two bytes a pixel: about 3 GB for 750 frames of 1080p, against about 0.5 GB at 768x448, and each preview frame takes longer to draw. Up to 6 GB is allowed -- about 1,500 frames of 1080p, or about 380 of a 4K clip; a clip longer than that stays at the map's size and the status line says so. Upscaling starts the Undo history afresh (Undo can't go back to the smaller map), and Load Clip goes back to the map's own size. A clip always opens at the map's size, so press Upscale Map again when you come back to it."
},
{
"part": "The Depth Map Editor · 15 Rendering and edit history",
"title": "Use in Splatting, and the edit history",
"href": "part3.html#upscale",
"text": "If you came from Splatting, Studio asks whether to use the render there. Use in Splatting puts the edited map in place of the original, so Splatting's preview and its batch both read it. Nothing is lost: the map it replaces, the original or an earlier edit, is kept in an edit_history folder beside the depth maps. To go back, edit again from the original or copy it back from there. Multi-Map ignores that folder. Good to knowEditing a clip that's already been edited starts from its original, so your saved layers are never applied twice."
},
{
"part": "The Depth Map Editor · 15 Rendering and edit history",
"title": "When something isn't right",
"href": "part3.html#upscale",
"text": "Erase leaves a smear or the object's edgeRaise Grow px so ROSE repaints a little past the object, or try another Seed; check the mask covers the whole object first. ROSE runs out of memoryUse a shorter Window or a smaller Size, and close other GPU jobs first. The mask drifts off the objectFix it on a frame where it's wrong, with Add or Remove, and track again. A halo of wrong depth around the objectRaise Grow a little, or use Edge snap. The change shows a hard lineRaise Feather. Holes inside the selectionTick Fill holes in the mask. Out of graphics memoryTick Keep video frames in system RAM, or Unload Model when you're done selecting. Find doesn't find itTry another word, or click the object instead."
},
{
"part": "Running jobs",
"title": "16 The job queue and progress",
"href": "part4.html#queue",
"text": "A job is one stage working through a folder of clips: a batch. Press Start and the stage works through every clip in its input folder, one after another, without you doing anything more."
},
{
"part": "Running jobs · 16 The job queue and progress",
"title": "The action bar",
"href": "part4.html#queue",
"text": "The bar at the bottom of the page shows the running job: The progress bar fills as the clip is worked on. Some stages work in steps, and the bar starts again for each: Merging, for instance, matches the grain first, then renders, then splits the left and right eyes if you asked for that. The line under it says what's happening: which clip, and how far through the batch. DepthCrafter and Inpainting also show the live numbers: which chunk or window of the clip they're on, and how long each step takes (s/it, seconds per step). The status line at the very bottom of the window says which stage is busy, whichever tab you're looking at, so you can look around the other stages while a job runs."
},
{
"part": "Running jobs · 16 The job queue and progress",
"title": "Upcoming Jobs",
"href": "part4.html#queue",
"text": "DepthCrafter, Splatting, Inpainting and Merging each list the clips the batch will work through, under Upcoming Jobs, with a Batch Progress counter above them. On DepthCrafter and Inpainting the list sits to the right of the settings. Each row shows the clip's length and its shape: 120 frames · 1.85:1. While a job runs, each row has its own bar: the clips already done are full, the one being worked on fills up, the rest are empty. The clips the run has finished turn light green, and stay green until the next run starts. When nothing is running, click a row to load that clip into the preview (Splatting and Merging: DepthCrafter and Inpainting have no preview). Tick clips to work on just those: Process Selected runs the batch on the ticked clips only. Start still runs them all. Move to Finished moves the ticked clips into the finished folders without processing them: handy for clips you've decided to skip. Restore Finished brings them back. Right-click a clip for quick jobs on that clip alone. In DepthCrafter: Process This Clip Now and Move to Finished. In Splatting: Splat This Clip Now (the same as Single, without loading it first) and Rerun in DepthCrafter, which switches to DepthCrafter with that clip as its input and the folder its depth map lives in as the output: change the settings and press Start, and the new map replaces the old one; and Rerun with Video Depth Anything, which does the same on DepthCrafter's VDA tab (the DepthCrafter map moves into a DepthCrafter folder beside it). In Inpainting: Inpaint This Clip Now and Move to Finished. In Merging: Merge This Clip Now and Move to Finished. Test Render, in every stage's right-click menu, runs that one clip as \"This Clip Now\" does but into a Test Renders folder inside the stage's output folder, so the real output is never overwritten. It moves nothing to finished and isn't passed on to the next stage as the job's result. Testing the same clip again replaces the earlier test. A clip the job just finished (its row green) also offers Send to Splatting, Send to Inpainting or Send to Merging, to take that one clip on through the pipeline without running the rest of the chapter. The next stage opens with what this one made as its input (DepthCrafter's depth map with its source clip, Splatting's Lo-Res splat, Inpainting's inpainted clip) and, when you're working by chapter, that chapter's folders for its output and its other inputs. Your settings are left alone: check them and press Start. The same menu plays the files that go with the clip in your usual video player: Play Source Clip (DepthCrafter, Splatting), Play Depth Map (Splatting), Play Lo-Res Splat and Play Hi-Res Splat (Inpainting), and Play Inpainted Clip, Play Original and Play Splat (Merging). These work during a job too. Under them, Open ... in Explorer opens a File Explorer window with that file selected, one for each kind of file that goes with the clip: Open Source Clip in Explorer and Open Depth Map in Explorer in Splatting, the Lo-Res and Hi-Res splats in Inpainting, and so on. Edge Fill's right-click offers the merged clip and its Hi-Res splat the same way."
},
{
"part": "Running jobs · 16 The job queue and progress",
"title": "When a batch ends",
"href": "part4.html#queue",
"text": "A message pops up saying how it went: how many clips were done, and how many failed or were skipped. If nothing could be made at all (an empty folder, missing model files), it says why instead. The next stage's Import from … button then offers this batch's output, so you can carry straight on. With a project picked in the title bar, an Import only offers that project's jobs, and fills every folder from the same chapter, the output folder included. The line under the progress bar then says how long the whole batch took, from Start to the end: Completed in 1h 02m 13s, or Stopped after … if you stopped it. It stays until that stage's line moves on. The messages that popped up are kept in the bell beside the gear, so one you missed while away can still be read. If Studio's window is minimized when a job finishes, Windows shows a notification with the same message, for every stage, Scene Cut included. Click it to bring Studio back. Nothing extra appears while the window is up. Good to knowStudio keeps its animations still while a job runs. Every redraw costs the graphics card a little, and on a long job that adds up, so the window stays calm until the job is done."
},
{
"part": "Running jobs",
"title": "17 Stopping safely",
"href": "part4.html#stopping",
"text": "There are two ways to stop: gently, keeping everything finished so far, or now."
},
{
"part": "Running jobs · 17 Stopping safely",
"title": "Stop After Clip: the gentle way",
"href": "part4.html#stopping",
"text": "Splatting, Inpainting and Merging have a Stop After Clip button. Press it and the clip being worked on is finished properly, then the batch stops. It reads Stopping After Clip until then; press it again if you change your mind. The rest of the folder is left for the next Start."
},
{
"part": "Running jobs · 17 Stopping safely",
"title": "Stop",
"href": "part4.html#stopping",
"text": "Stop asks first, and offers a choice: Stop now: the clip being processed is cut off and its unfinished output is thrown away. Clips already finished are kept. The safe option, which depends on the stage: Splatting, Merging, Inpainting: Stop after this clip (the same as Stop After Clip). Inpainting, while it works through a clip in chunks: Stop after this chunk. The piece being worked on is kept, and the next Start on that clip carries on from there. DepthCrafter: Stop after this segment, when it's processing in segments. Resume carries on from the next segment. Keep running: nothing changes."
},
{
"part": "Running jobs · 17 Stopping safely",
"title": "Carrying on later",
"href": "part4.html#stopping",
"text": "You never lose finished work. Every finished clip is moved into a finished folder (Part 1), so pressing Start on the same chapter again carries on with the clips that are left. A clip cut off by Stop now is never left behind looking finished: its unfinished file is thrown away."
},
{
"part": "Running jobs · 17 Stopping safely",
"title": "Closing Studio during a job",
"href": "part4.html#stopping",
"text": "Closing the window while a job runs asks first. Quit now stops the job the same way as Stop now, then closes Studio. To let the clip finish first, press Stop After Clip and close Studio once it has stopped."
},
{
"part": "Running jobs",
"title": "18 GPU memory",
"href": "part4.html#gpu",
"text": "Every stage runs on your graphics card and needs a lot of its memory (VRAM), so Studio looks after it for you."
},
{
"part": "Running jobs · 18 GPU memory",
"title": "One job at a time",
"href": "part4.html#gpu",
"text": "Only one stage can run a job at once. Start another while one is running and Studio tells you to wait for the first to finish. This includes Scene Cut and the Depth Map Editor's renders. When a job starts, any other stage still holding the graphics card, such as another stage's preview, lets go of it first, so the job has the memory to itself. A preview that was put away comes back once the job is over."
},
{
"part": "Running jobs · 18 GPU memory",
"title": "The VRAM meter",
"href": "part4.html#gpu",
"text": "The VRAM meter at the top right shows how much of your card's memory is in use: by every program, not only Studio. It updates every couple of seconds. Click it for the details: how much is free, and how much Studio itself is using. TipGames, video players and browsers use graphics memory too. If a job runs out of memory, or crawls, close them, or check the meter to see how much is left."
},
{
"part": "Running jobs · 18 GPU memory",
"title": "Running out of memory",
"href": "part4.html#gpu",
"text": "Each stage's \"When something isn't right\" table in Part 2 says which settings to lower. In short: a smaller size or batch, fewer frames at a time, or an offload mode that keeps part of the model in normal memory instead."
},
{
"part": "Running jobs",
"title": "19 Unattended runs",
"href": "part4.html#unattended",
"text": "A chapter can take hours, and Studio is built to work through it on its own while you're away."
},
{
"part": "Running jobs · 19 Unattended runs",
"title": "Before you leave it",
"href": "part4.html#unattended",
"text": "Set every clip up first. Go through the clips in the preview and save each one's sidecar (Splatting, Merging), so the batch uses your settings for every shot. Check the first clip. Let one clip finish and look at it before leaving the rest to run: a wrong setting is cheaper to catch on one clip than on fifty. Stop Windows from sleeping. A sleeping PC pauses the job. In Windows' power settings, set Sleep to Never while you convert (it is okay for the monitor to be turned off). Close what you don't need, especially anything that uses the graphics card."
},
{
"part": "Running jobs · 19 Unattended runs",
"title": "When you come back",
"href": "part4.html#unattended",
"text": "The message that popped up when the batch ended says how many clips were done and how many failed. The console (Ctrl+`) has the whole run, clip by clip, each one starting with a separator line. A failed clip says why there. Studio also writes everything to studio_last_run.log in your Studio folder. The run before that is kept as studio_last_run.prev.log, so a log is still there after Studio is started again. Clips that failed are still in the input folder (they weren't moved to finished), so pressing Start again tries just those. If Studio closed by itselfWhen Studio ends because of an error, a window offers to open the log. Keep that file: it's what explains what happened, and the first thing to send when asking for help."
},
{
"part": "Customising",
"title": "20 Settings and themes",
"href": "part5.html#themes",
"text": ""
},
{
"part": "Customising · 20 Settings and themes",
"title": "The theme popout",
"href": "part5.html#themes",
"text": "The theme button at the top of the window, beside the stage tabs, shows the theme you're using. Click it for everything about the look: Themes, in two groups. Dark themes: Graphite, True Black, The Nile, Ink, Nord, Warm Carbon, Plum, Teal Slate and Moss. Light themes: Light, Paper, Frost, Orchid, Sea Glass, Bone and Fern. Click one and the window changes straight away. Frosted Glass: your desktop shows through behind the window, blurred. The Opacity knob sets how much of the theme's colour stays over the glass (30 to 90%); lower is more see-through. Double-click it for Auto, the theme's own setting. UI Scale makes everything bigger or smaller, from 75% to 100%. Double-click the knob for Auto, which picks a size to suit your screen. Section Outlines: a thin outline around each panel. Film Grain: a fine film grain over the window and its popups, tuned for each theme. The video previews are always kept clean, so it's never mistaken for grain in the picture. App Icon: Studio's icon in fifteen colours, Matte or Glossy, or the Classic one. It changes live on the window, the title bar, the taskbar, the notifications and Studio's shortcuts on the desktop and the taskbar (see making a desktop shortcut). TipOn a light theme, keep the glass Opacity at 60% or more: below that, the desktop behind can make the text harder to read. Want other colours?You can add as many themes of your own as you like. Studio's extra themes are listed in studio/shell.py, under _EXTRA_THEMES; the notes above it say what each colour is for. Send that file to an AI chat assistant (an LLM) and tell it the colours you'd like, for example \"add a dark theme called Forest, deep green with a white accent\". Put the file it gives you back in place and restart Studio: the new theme is in the list. Keep a copy of your version, as updating Studio can replace the file."
},
{
"part": "Customising · 20 Settings and themes",
"title": "The Settings window",
"href": "part5.html#themes",
"text": "The gear (Ctrl+,) opens Settings. The list on the left has: First, the options of the stage you're looking at, so the list changes as you change tabs. Each stage has a File group (see the next section), and most have a Help group with Debug Logging, which writes much more detail to the console, useful when asking for help. Studio: Save All Settings, Set Project Folder, Check for Updates, Open Console and Exit. Appearance: Show Tips (the tips above each stage's progress bar), Fit Window to Content (Ctrl+0), and the Projects Folder and TV Shows Folder (Part 1). Keyboard Shortcuts: every shortcut, by where it works. GPU: Release GPU Memory unloads the models the stages are keeping on the graphics card, to free it for something else. It can't be used while a job is running."
},
{
"part": "Customising · 20 Settings and themes",
"title": "The window",
"href": "part5.html#themes",
"text": "Studio remembers the window's size and place, and grows the window when a page needs more room to show everything. If you've resized it by hand and want it back to the page's own size, press Ctrl+0."
},
{
"part": "Customising",
"title": "21 Saving and loading settings",
"href": "part5.html#saving",
"text": ""
},
{
"part": "Customising · 21 Saving and loading settings",
"title": "Saved for you",
"href": "part5.html#saving",
"text": "You don't have to save anything. Every stage keeps its settings as you change them, and Studio saves them all when you close it, along with the theme, the window, and where each stage was in each project. Next time, everything is as you left it. Settings → Studio → Save All Settings writes them all to disk straight away, if you'd rather not wait."
},
{
"part": "Customising · 21 Saving and loading settings",
"title": "Three kinds of settings",
"href": "part5.html#saving",
"text": "A stage's settingsWhat you see on the page. They apply to every clip, unless the clip has its own. A clip's sidecarOne clip's own settings, saved beside it in a .fssidecar file (Part 2, 8f). They win over the stage's settings for that clip, and they travel with it. A settings fileA copy of a stage's settings you save yourself, to keep or to share (below)."
},
{
"part": "Customising · 21 Saving and loading settings",
"title": "Settings files",
"href": "part5.html#saving",
"text": "Each stage's File group in Settings (Inpainting V2 has the buttons on its page) can: Save Settings to a file: a snapshot of the stage's settings. Keep one for a look you like, or give it to someone else so they get the same result. Load Settings from a file: put a saved snapshot back. Reset to Default: every setting of that stage back to how Studio came. It asks first. TipBefore trying something very different on a stage, save its settings to a file first. If you don't like the result, load the file and you're back where you were."
},
{
"part": "Customising · 21 Saving and loading settings",
"title": "Sidecar options (Splatting)",
"href": "part5.html#saving",
"text": "Splatting's File group also decides how sidecars are used: Update Slider from Sidecar: when you move to a clip that has a sidecar, the page's knobs show its values. Auto Save Sidecar on Next: changes to a clip that already has a sidecar are saved into it as you move to another clip, and keyframe edits straight away. A clip without a sidecar still needs Create Sidecar the first time. Sidecars to Source and Sidecars to Depth move all the sidecars from the Depth Maps folder to the Source Clips folder, or back. Both ask first."
},
{
"part": "Reference",
"title": "A Recommended settings by graphics card",
"href": "appendices.html#cards",
"text": "Studio's defaults are set for a card with 16 GB of graphics memory (VRAM). With less, a few settings need turning down; with more, a few can go up for speed. Any NVIDIA RTX card can run Studio (Part 1); what changes with the card is how much fits at once. Tested onStudio has been tested on a machine with an RTX 5060 Ti with 16 GB of VRAM and 32 GB of system RAM. The 16 GB settings below are what runs there; the others are where to start on other cards. Starting pointsEvery movie is different, and long or high-resolution clips need more memory than short ones. Use these as a place to start: if a stage runs out of memory, turn the next setting in its list down one step and try again. The VRAM meter at the top right (Part 4) shows how close a job comes to the limit."
},
{
"part": "Reference · A Recommended settings by graphics card",
"title": "How much each stage needs",
"href": "appendices.html#cards",
"text": "Scene CutLight. Any RTX card. DepthCrafterHeavy. Grows with the Resolution Preset and the Window Size. SplattingModerate. Grows with the Batch size. Inpainting (V1)Heavy. Grows with the frame size and Frames Chunk. Inpainting V2Very heavy. Needs 20 GB or more on the card, or lots of system memory instead (below). MergingLight to moderate -- unless Use GPU for Mask Processing is on with a large Batch Chunk Size: then it can hold most of a 16 GB card for the whole batch. Off, it stays at a couple of GB, and most of a merge is the encode anyway. Depth Map EditorSAM 3 takes about 4 GB, and stays loaded until you unload it. Erase's ROSE needs more at larger Size and longer Window settings."
},
{
"part": "Reference · A Recommended settings by graphics card",
"title": "16 GB: the defaults",
"href": "appendices.html#cards",
"text": "DepthCrafter: the 768 presets, CPU Offload Mode model. Splatting: a full-size Batch of 10 is safe; up to about 20 works, and about 30 for the low-res pass. Inpainting: Frames Chunk 25, Tile Number 2 (the defaults)."
},
{
"part": "Reference · A Recommended settings by graphics card",
"title": "12 GB and less",
"href": "appendices.html#cards",
"text": "Turn these down, in this order, until the stage runs: DepthCrafter CPU Offload Mode to sequential (slower, much less memory). Turn on Process as Segments (the low-VRAM mode, Part 2), and lower the Window Size. Stay on the 768 presets. Splatting: lower the full-size Batch, halving it each time until it runs; a smaller low-res size helps too. Inpainting CPU Offload to sequential. Lower Frames Chunk below 25. Raise Tile Number above 2 (3 fills each frame in nine pieces). Depth Map Editor: unload SAM 3 (Settings → SAM 3) before starting another stage's job, so its 4 GB are free."
},
{
"part": "Reference · A Recommended settings by graphics card",
"title": "24 GB and more",
"href": "appendices.html#cards",
"text": "DepthCrafter: CPU Offload Mode none (no offloading) is the fastest. Don't go to the bigger presets just because they fit: 768 still gives the best depth for most scenes (Part 2). Splatting: bigger batches, if the job gets faster with them. Inpainting: stay on V1. V2 is experimental on any card, even a 32 GB RTX 5090: use it only for problem scenes V1 can't handle, never for a whole conversion."
},
{
"part": "Reference · A Recommended settings by graphics card",
"title": "Inpainting V2's offload modes",
"href": "appendices.html#cards",
"text": "For the problem scenes you try it on (V2 is experimental, Part 2), V2's VRAM / Performance panel decides where its big model lives while it works. Pick by your card, and by your system memory (RAM): NoneEverything on the card. The fastest, but needs about 28 GB of VRAM. Transformer onlyThe largest part streams from RAM. For 24 GB cards and up. FP8 residentA smaller 8-bit copy, all on the card. Fastest when it fits: about 20 GB of VRAM, an RTX 40 or 50 series card, and the separate StereoCrafter2-FP8 download. Group offloadEverything streams from RAM. Runs on little VRAM, slowly, and needs about 28 GB of RAM. Group offload (disk)The same, from disk: the least RAM, the slowest. Keep its cache on a fast NVMe drive. MMGP streaming (int8)Shrinks the model to 8-bit as it loads, then streams it from RAM. About 30% faster than Group offload on about 4 GB of VRAM, so it suits 8 and 12 GB cards too. It holds about 28-31 GB of RAM while it runs (Windows locks at most 40% of your RAM for it; the rest stays ordinary memory), so 32 GB of RAM runs it; loading briefly spills into the page file. Remember V2's model files take 46.6 GB of disk space, and FP8 resident another 17.4 GB (Part 2)."
},
{
"part": "Reference · A Recommended settings by graphics card",
"title": "System memory (RAM)",
"href": "appendices.html#cards",
"text": "Offloading moves work from the graphics card to your computer's own memory, so a card with less VRAM leans on RAM more. If the whole computer slows to a crawl during a job, it has run out of RAM: close other programs, or offload less. Inpainting V2's streaming modes want 48 GB of RAM or more."
},
{
"part": "Reference",
"title": "B File names",
"href": "appendices.html#files",
"text": ""
},
{
"part": "Reference · B File names",
"title": "In your Studio folder",
"href": "appendices.html#files",
"text": "StereoCrafter Studio.exeStarts Studio. StereoCrafter Studio.batStarts Studio too, as a script. config/Your settings: studio.json for the theme, the window and your projects, and one file per stage (depthcrafter_auto_sizes.json holds DepthCrafter's Auto Size picks). To put a stage back to its defaults, use Reset to Default (Part 5). weights/The AI models' files. weights/ROSE holds Erase's ROSE and its own environment (Part 3). studio_last_run.logEverything from the last time Studio ran; the run before it is studio_last_run.prev.log (Appendix C). last_jobs.jsonWhat each stage made last, for the Import from … buttons. dependency/grain_by_theme.jsonThe film grain, tuned for each theme. studio/Studio's own program files. studio/shell.py holds the extra themes (Part 5). THIRD_PARTY_NOTICES.mdThe licences of the code included with Studio."
},
{
"part": "Reference · B File names",
"title": "In your project",
"href": "appendices.html#files",
"text": "The folders are in Part 1. Each stage names its files after the clip, and adds an ending: DepthCrafter<clip>_depth.mp4: the depth map. While it works in segments, the pieces are <clip>_depth_1of4.npz and so on. SplattingIn hires and lowres: <clip>_<width>_splatted4 holds four pictures in one frame (the original, the depth, the holes and the shifted eye); _splatted2 holds two (the holes and the shifted eye), with Dual Output Only. A D after the number means Dual Eye, an F a flipped clip. With DNxHR on, the full-size file is a .mov; with Container set to MKV, the files are .mkv. InpaintingEnds in _inpainted_sbs (both eyes side by side), _inpainted_right_eye (from a two-picture splat) or _inpainted_dual_sbs (Dual Eye). MergingEnds in the format you chose: _merged_full_sbs, _merged_half_sbs, _merged_full_sbsx (cross-eye), _merged_right_eye, or _merged_anaglyph."
},
{
"part": "Reference · B File names",
"title": "Beside your clips",
"href": "appendices.html#files",
"text": ".fssidecarA clip's own settings (Part 2, 8f): <clip>_depth.fssidecar beside the depth map, or <clip>.fssidecar beside the source clip. <clip>_submask.mp4The clip's Detect Text mask, beside the source clip (Part 2, 8e). It's never treated as a clip. .partialA file still being written. If a job is stopped, it's thrown away, so a half-made file never looks finished. finished/Clips a stage has already used (Part 1). edit_history/What an edit replaced: depth maps the Depth Map Editor's render replaced, beside the depth maps, and the original of a clip Erase from Clip changed, with its erase mask, beside the clip (Part 3)."
},
{
"part": "Reference",
"title": "C Troubleshooting and log files",
"href": "appendices.html#troubleshooting",
"text": "Each stage has its own \"When something isn't right\" table in Part 2, and the Depth Map Editor's is at the end of Part 3. This appendix covers Studio as a whole, and where to look when something goes wrong."
},
{
"part": "Reference · C Troubleshooting and log files",
"title": "Where to look",
"href": "appendices.html#troubleshooting",
"text": "The messageWhen a batch ends, a message says how many clips were done and how many failed, or why nothing could be made. The consoleCtrl+`. The whole run, line by line, one tab per stage (Part 1). A failed clip says why here. Debug LoggingIn each stage's Help group in Settings. The console then shows much more detail. Turn it on, run the problem again, and the extra lines usually show where it went wrong. The log filestudio_last_run.log in your Studio folder has everything from the last time Studio ran. The run before that is kept as studio_last_run.prev.log."
},
{
"part": "Reference · C Troubleshooting and log files",
"title": "Common problems",
"href": "appendices.html#troubleshooting",
"text": "Studio won't startA window says what went wrong and offers to open the log. If it says the graphics card can't be used, update the NVIDIA driver (version 580 or newer, Part 1). \"Studio could not be started\"Another Studio is probably still running and holding the log file. If you can't see its window, check Task Manager for StereoCrafter Studio or python and end it, then start Studio again. The chapter list is emptyThe project folder isn't laid out the way Studio expects: check the stage folders' names and that the chapter folders start with Chapter and a number (Part 1). A stage says there are no clipsThey may all be in its finished folder already. Restore Finished in the stage's File group brings them back. A stage says its model files are missingThe weights aren't where the stage looks: check the weights folder (Part 1, and Scene Cut's in Part 2). \"Driver does not support the required nvenc API version\"Your copy of ffmpeg is newer than your NVIDIA driver supports for video encoding (the message names the driver it needs). Update the driver, or use an ffmpeg build from before it, and check with ffmpeg -version which one Windows finds first. Choosing the CPU encoder in Encoding Options works meanwhile, more slowly. Out of memoryAppendix A, and the stage's own table in Part 2. Everything is slowLook at the VRAM meter: another program may be using the graphics card. A computer that runs out of system memory slows to a crawl too. Another stage won't startOnly one job runs at a time (Part 4). Wait for it, or stop it. Videos won't encodeFFmpeg must be installed and on your PATH (Part 1)."
},
{
"part": "Reference · C Troubleshooting and log files",
"title": "Asking for help",
"href": "appendices.html#troubleshooting",
"text": "Send these, and the problem can usually be found quickly: The log file, studio_last_run.log (or .prev.log if you've started Studio again since). What you did, and what you expected to happen. Your graphics card and how much memory it has. For one stage, the console lines from that stage's tab, with Debug Logging on. Select the lines and press Ctrl+C, or use the copy button for the whole tab. Before you sendLogs contain your folders' names, such as your movie's title. Look through the file first if that matters to you."
},
{
"part": "Reference",
"title": "D Preview views",
"href": "appendices.html#views",
"text": "Splatting's and Merging's previews can show the same frame in many ways. You pick two of them, Front and Back, and click the picture to flip between them. None of the views change what is saved: they are ways of looking at the frame."
},
{
"part": "Reference · D Preview views",
"title": "Judging the 3D",
"href": "appendices.html#views",
"text": "Dubois AnaglyphRed/cyan 3D with the least ghosting and the best colour an anaglyph can give. Wear red/cyan glasses. The usual Front, and the best everyday view on an ordinary screen. Optimized AnaglyphRed/cyan 3D that keeps more of the colour, with a little more ghosting. For glasses or screens where Dubois looks too dark. Anaglyph 3DThe plain red/cyan mix: the left eye in grey, the right eye in colour. Strong depth, little colour. Good for checking the depth alone. ColorCode (Amber/Blue) and (Blue/Amber)3D for amber/blue ColorCode glasses. The two put the amber on different eyes; pick the one that matches your glasses. Side-by-SideBoth eyes next to each other at full size, left then right, so the picture is twice as wide. For viewing on a 3D TV or monitor (see Full Preview), or with the parallel free-viewing method. X swaps the eyes. Side-by-Side (Cross-eye)The same with the eyes swapped, for the cross-eyed free-viewing method. WigglegramThe picture flips between the two eyes, so depth shows as motion without any glasses. Wiggle Speed sets how fast it flips. It holds still while a job runs. Glasses-Free 3DReal 3D on a glasses-free 3D monitor, each eye at full resolution. Read the section below before you rely on it."
},
{
"part": "Reference · D Preview views",
"title": "Looking at the pieces (Splatting)",
"href": "appendices.html#views",
"text": "Splat ResultThe new right eye on its own, as Splatting makes it. The black gaps are the holes: places the left eye never saw, which Inpainting fills later. The usual Back. Highlight Holes paints them. Splat Result(Low)The same at the Low Res size, the copy Inpainting works on. Occlusion Mask and (Low)The holes alone, in white: exactly what Inpainting will be asked to fill. Original (Left Eye)The source frame, untouched. It is the left eye of the result. Depth Map and Depth Map (Color)The depth map as Splatting reads it, after its depth settings. The colour version makes small steps and noise easier to see. Both show the depth's lowest and highest values. Boost ZoneWhere Pop Out Boost acts, and how strongly. Orange is boost in use; cyan shows what would be boosted while its Amount is 0. An all-dark picture means the threshold is above anything in the clip. Mesh WarpA different way to make the right eye: the picture laid on a 3D mesh shaped by the depth, filmed by two virtual cameras. It stretches instead of leaving holes. For comparison; its settings are in Splatting & Output Settings. SBS + MeshSide-by-Side above Mesh Warp, to compare the two."
},
{
"part": "Reference · D Preview views",
"title": "Looking at the pieces (Merging)",
"href": "appendices.html#views",
"text": "Blended ImageThe finished right eye: the splatted eye with the inpainted fills blended into its holes. The usual Back. Warped (Right BG)The splatted right eye before filling, holes and all. Inpainted Right EyeInpainting's result on its own. Processed MaskWhere the fills go, after Merging's mask settings. Highlight Mask in Preview draws it over the picture instead. Original (Left Eye)The source frame. Depth MapThe depth, when the splatted clip carries it (the four-panel layout). Mesh WarpAs in Splatting, for comparison. Merging also has the anaglyphs, Side-by-Side, Wigglegram and Glasses-Free 3D, built from the original left eye and the blended right eye."
},
{
"part": "Reference · D Preview views",
"title": "Overlays",
"href": "appendices.html#views",
"text": "Crosshair (with White and Multi) marks the screen centre, on the anaglyph views and Glasses-Free 3D, to help set Convergence: anything that lines up with it sits at screen depth. Highlight Holes (Splatting) and Highlight Mask in Preview (Merging) paint the holes or the mask on the views that show the right eye."
},
{
"part": "Reference · D Preview views",
"title": "Glasses-Free 3D",
"href": "appendices.html#views-glasses",
"text": "On a glasses-free 3D monitor built on Leia's SR platform (Samsung's Odyssey 3D, for one), this view shows the preview in real 3D with no glasses. Only the picture turns 3D; the rest of the window stays as it is. Each eye goes to the monitor at full resolution, not squeezed into half a side-by-side. It is also in Full Preview, and in the Depth Map Editor's 3D view. It works differently from the other views, so a few things are worth knowing. It needs the monitor, and the window on it. With no compatible monitor connected, or with its SR software not running, the view is greyed out in the list, and its tip says so. With the monitor connected but Studio's window on another screen, it is greyed out too: move the window onto the 3D monitor. Full Preview has to open on that monitor as well. The 3D lens is the whole monitor's. These monitors switch a lens layer on over the entire screen. While the view is showing, everything else on that monitor (Studio's controls, other windows) is seen through the lens and can look jagged. The lens switches off when you choose another view or leave the page. The camera follows your eyes, only while it is live. The monitor's eye-tracking camera keeps the 3D lined up as you move. Studio runs it only while the view is showing and no job is running. Choose another view, or leave the page, and the camera turns off a second later. During a job it holds still. While any stage is running a job, nothing in the preview may redraw, so the 3D freezes on its last frame and the camera turns off. The frozen picture is lined up for where you sat when it froze; move and it can ghost until the job ends. It goes live again after the job. If the picture changes or the window moves during a job, the plain picture shows instead until then. Where you sit matters. Sit roughly centred, at a normal distance. Far to one side, very close, or moving quickly, the eyes can ghost or swap for a moment while the tracking catches up. What full resolution means here. Studio sends each eye at the clip's full size. The monitor itself divides its pixels between your two eyes, so what each eye sees is less sharp than the same monitor in 2D; that is how these monitors work. For the closest look at the pixels, set Preview Scale to 100%. Fit and zoom resize the picture, as they do in 2D. Some things stay 2D. Drop-down lists, tips, dialogs and notifications that sit over the picture show normally: the 3D gets a hole where they are. Turning the card over shows the plain picture for a moment. It shows what will be saved. Borders and Frame Break are in it, as in the output. The X eye swap doesn't apply (it would turn the depth inside out on the monitor). Highlight Holes and Highlight Mask are drawn on the right eye only, so one eye sees them; they shimmer, which is expected. A little slower than an anaglyph. Two full eyes leave the graphics card instead of one mixed picture, so sliders update a little more slowly than in Dubois. After Studio starts it takes a few seconds to find the monitor; until then the view is greyed out. For checking, not gradingGlasses-Free 3D is the closest thing to watching the result, but judge colour and fine detail in 2D, and check the final files on the screen they are made for."
},
{
"part": "Reference",
"title": "E Glossary",
"href": "appendices.html#glossary",
"text": "BatchA stage working through a folder of clips, one after another. Also: how many frames go to the graphics card at once. ChapterA part of the movie with a folder of its own in each stage folder (Part 1). ChunkA run of frames worked on together. Inpainting fills a clip one chunk at a time. ClipOne shot of the movie, in a file of its own. Scene Cut makes them. ConvergenceThe depth that sits exactly on the screen. Nearer things pop out of it; farther things sit behind it. CRFThe quality setting of a video encoder. Lower is better quality and bigger files. Depth mapA black-and-white video where brightness is distance: white is near, black is far. DepthCrafter makes them. DisparityHow far the two eyes' pictures are shifted apart: the strength of the 3D. DNxHRA video format made for editing that keeps the picture visually lossless. Very large files. Dual EyeBuilding both eyes from your video, the shift split between them, instead of keeping the original as the left eye. Finished folderWhere a stage moves a clip once it has used it, so a restart carries on with the clips left (Part 1). HoleA gap in the shifted picture where the other eye sees something the original camera didn't. Inpainting fills the holes. Inference stepsHow many passes an AI model makes over each piece. More is slower and, up to a point, better. InpaintingFilling the holes with picture that fits around them. KeyframeA setting's value at one frame of a clip. After a static keyframe the value holds; into a dynamic one it travels from the keyframe before, along the shape set in the Shaper (a ramp, an ease, an LFO or your own curve). MaskA black-and-white picture marking an area: white is in, black is out. OffloadKeeping part of an AI model in the computer's own memory instead of the graphics card's, to fit a smaller card. Slower. Put backLaying an object Erase took out back into the finished 3D, at a depth of its own, in Merging. Project folderOne folder per movie or show, with a folder for each stage inside (Part 1). RainSplatting's Rain section: the rain separated from the scene and put back at depths of its own (Part 2). ROSEThe AI video model the Depth Map Editor's Erase uses to take an object, and its shadow or reflection, out of a clip. SBSSide-by-side: the left and right eye's pictures next to each other in one frame. Full SBS keeps each at full width; Half SBS squeezes both into one frame's width. SeedThe starting point of an AI model's randomness. The same seed and settings give the same result again. ShaperWhere you set how a dynamic keyframe's values travel into it: Linear, an ease, an LFO or Custom points. SegmentA piece of a long clip that DepthCrafter works on separately, then joins back (Part 2). SidecarA small .fssidecar file beside a clip, holding that clip's own settings (Part 2). SplattingShifting each pixel by its depth to make the other eye's picture. VRAMThe graphics card's own memory. Most of Studio's limits come from it. WeightsAn AI model's files: what it has learned. They're downloaded separately and go in the weights folder."
},
{
"part": "Reference",
"title": "F Credits",
"href": "appendices.html#credits",
"text": "Studio stands on the work of many people. Thank you to all of them."
},
{
"part": "Reference · F Credits",
"title": "The research",
"href": "appendices.html#credits",
"text": "StereoCrafter: Sijie Zhao, Wenbo Hu, Xiaodong Cun, Yong Zhang, Xiaoyu Li, Zhe Kong, Xiangjun Gao, Muyao Niu and Ying Shan, Tencent AI Lab and ARC Lab, Tencent PCG. stereocrafter.github.io DepthCrafter, Tencent: the depth maps. github.com/Tencent/DepthCrafter Stable Video Diffusion, Stability AI: the model Inpainting V1 is built on. Wan 2.1 VACE: the model Inpainting V2 is built on. SAM 3, Meta: the Depth Map Editor's object selection and tracking. github.com/facebookresearch/sam3 TransNetV2, AutoShot (wentaozhu/AutoShot) and OmniShotCut (UVA Computer Vision Lab): Scene Cut's shot detection. U²-Net: the small network behind Splatting's Auto-Converge. ROSE, Kunbyte AI, built on Alibaba PAI's Wan2.1-Fun: the Depth Map Editor's Erase. github.com/Kunbyte-AI/ROSE"
},
{
"part": "Reference · F Credits",
"title": "The tools before Studio",
"href": "appendices.html#credits",
"text": "enoky: the StereoCrafter GUI suite and installer (github.com/enoky/StereoCrafter), and SceneCutGUI (github.com/enoky/SceneCutGUI), which Scene Cut is ported from. Billynom8: DepthCrafter GUI Seg (github.com/Billynom8/DepthCrafter_GUI_Seg)."
},
{
"part": "Reference · F Credits",
"title": "StereoCrafter Studio",
"href": "appendices.html#credits",
"text": "Built by subtlewubs."
},
{
"part": "Reference · F Credits",
"title": "The example footage",
"href": "appendices.html#credits",
"text": "Most of the screenshots show frames from Tears of Steel, (CC) Blender Foundation | mango.blender.org, used under the Creative Commons Attribution 3.0 licence. The film was cut into short clips and run through Studio for these pages, so what you see has been changed: converted to 3D, its depth edited, and in one clip a man erased."
},
{
"part": "Reference · F Credits",
"title": "Built with",
"href": "appendices.html#credits",
"text": "PyTorch, Qt and PySide6, FFmpeg, Hugging Face Diffusers and Transformers, decord, OpenCV, SageAttention and MMGP. The licences of the code included with Studio are listed in THIRD_PARTY_NOTICES.md in your Studio folder."
}
];
