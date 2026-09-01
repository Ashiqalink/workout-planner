"""The single source of truth for every user-adjustable setting in FitTrack.

Everything downstream is generated from the ``SETTINGS`` list below:

* the ``/settings`` page renders its groups, controls and help text,
* ``/api/settings`` validates and coerces incoming values,
* the natural-language settings assistant builds its candidate list here, so a
  setting becomes askable the moment it is registered — there is no second
  place to update,
* ``learn/`` docs and the command palette read the same labels.

Adding a feature toggle therefore means adding one ``Setting(...)`` entry and
honouring it wherever it applies. Nothing else needs to change.

Two ideas keep the surface small while the option count stays large:

``tier``
    ``simple`` settings are always visible, ``advanced`` appear once the user
    opts into Advanced mode, ``expert`` only in Everything mode. Search always
    covers every tier, so a hidden setting is still findable by name — this is
    progressive disclosure, not burial.

``keywords``
    Everyday phrasings a person might use for the setting ("night mode", "make
    the text bigger"). The deterministic matcher in ``match_intent`` scores
    against these, which is what lets the assistant work with no model running.
"""

import re
from dataclasses import dataclass, field
from typing import Any, Optional


# ── Setting definition ───────────────────────────────────────────────

@dataclass(frozen=True)
class Setting:
    """One user-adjustable option.

    ``type`` decides both the control rendered on the settings page and the
    coercion applied in :func:`coerce`:

    ``bool``   on/off switch
    ``enum``   one of ``choices`` — a list of ``(value, label)`` pairs
    ``multi``  any subset of ``choices``, stored as a list
    ``int``    whole number clamped to ``minimum``/``maximum``
    ``float``  decimal clamped the same way
    ``text``   free text, truncated to ``maximum`` characters
    """
    key: str
    label: str
    group: str
    type: str
    default: Any
    tier: str = 'advanced'
    help: str = ''
    choices: tuple = ()
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    step: Optional[float] = None
    unit: str = ''
    keywords: tuple = field(default=())
    # True for bool settings named for *suppressing* the thing their keywords
    # describe: "turn off animations" means Reduce motion goes ON. The intent
    # matcher flips the requested direction for these.
    intent_inverted: bool = False

    @property
    def choice_values(self):
        return [c[0] for c in self.choices]

    def label_for(self, value):
        """Human label for a stored value — used to describe changes in prose."""
        if self.type == 'bool':
            return 'on' if value else 'off'
        if self.type == 'multi':
            if not value:
                return 'nothing'
            labels = {c[0]: c[1] for c in self.choices}
            return ', '.join(labels.get(v, str(v)) for v in value)
        if self.type == 'enum':
            for val, lab in self.choices:
                if val == value:
                    return lab
        if self.unit:
            return f'{value}{self.unit}'
        return str(value)


# Truthy / falsy words the assistant and the API both accept for switches.
_TRUE_WORDS = {'1', 'true', 'yes', 'y', 'on', 'enable', 'enabled', 'show',
               'always', 'active', 'allow'}
_FALSE_WORDS = {'0', 'false', 'no', 'n', 'off', 'disable', 'disabled', 'hide',
                'never', 'inactive', 'none', 'stop', 'block'}

WEEKDAYS = (
    ('monday', 'Monday'), ('tuesday', 'Tuesday'), ('wednesday', 'Wednesday'),
    ('thursday', 'Thursday'), ('friday', 'Friday'), ('saturday', 'Saturday'),
    ('sunday', 'Sunday'),
)

DOMAIN_CHOICES = (
    ('Strength', 'Strength & Power'),
    ('Speed & Mobility', 'Speed & Mobility'),
    ('Endurance', 'Endurance'),
    ('Agility', 'Agility'),
    ('Cognitive', 'Cognition'),
)

# Ordered roughly by how likely someone is to have it. The first block is what
# almost any room provides, which is why those are on by default: the shipped
# library needs nothing else, so the filter is a no-op until a user adds a
# custom exercise that requires real equipment.
EQUIPMENT_CHOICES = (
    ('none', 'Nothing at all'),
    ('chair', 'Chair or desk'),
    ('wall', 'Wall space'),
    ('doorway', 'Doorway'),
    ('stairs', 'Stairs'),
    ('step', 'Step or low box'),
    ('mat', 'Exercise mat'),
    ('towel', 'Towel'),
    ('open_space', 'Room to move'),
    ('dumbbells', 'Dumbbells'),
    ('kettlebell', 'Kettlebell'),
    ('resistance_band', 'Resistance band'),
    ('pullup_bar', 'Pull-up bar'),
    ('jump_rope', 'Jump rope'),
    ('bench', 'Bench'),
    ('cones', 'Cones or markers'),
)

EQUIPMENT_DEFAULT = ['none', 'chair', 'wall', 'doorway', 'stairs', 'step',
                     'mat', 'towel', 'open_space']


# ── The registry ─────────────────────────────────────────────────────

SETTINGS = [

    # ── Appearance ───────────────────────────────────────────────────
    Setting(
        key='appearance.theme', label='Theme', group='Appearance', tier='simple',
        type='enum', default='system',
        choices=(('system', 'Match my device'), ('light', 'Light'),
                 ('dark', 'Dark'), ('midnight', 'Midnight'),
                 ('sepia', 'Warm paper'), ('contrast', 'High contrast')),
        help='Colour scheme for the whole app.',
        keywords=('dark mode', 'light mode', 'night mode', 'theme', 'colour scheme',
                  'color scheme', 'darker', 'lighter', 'black background',
                  'white background', 'midnight', 'high contrast'),
    ),
    Setting(
        key='appearance.accent', label='Accent colour', group='Appearance', tier='simple',
        type='enum', default='teal',
        choices=(('teal', 'Teal'), ('blue', 'Blue'), ('violet', 'Violet'),
                 ('rose', 'Rose'), ('amber', 'Amber'), ('lime', 'Lime'),
                 ('slate', 'Slate')),
        help='Highlight colour for buttons, rings and charts.',
        keywords=('accent', 'accent colour', 'accent color', 'highlight colour',
                  'button colour', 'brand colour', 'primary colour'),
    ),
    Setting(
        key='appearance.density', label='Spacing', group='Appearance', tier='simple',
        type='enum', default='comfortable',
        choices=(('comfortable', 'Comfortable'), ('cozy', 'Cozy'), ('compact', 'Compact')),
        help='How much breathing room the layout uses.',
        keywords=('density', 'spacing', 'compact', 'tighter', 'roomier',
                  'more on screen', 'padding'),
    ),
    Setting(
        key='appearance.font_size', label='Text size', group='Appearance', tier='simple',
        type='int', default=15, minimum=12, maximum=22, step=1, unit='px',
        help='Base font size. Everything scales from this.',
        keywords=('font size', 'text size', 'bigger text', 'smaller text',
                  'larger font', 'readable', 'zoom'),
    ),
    Setting(
        key='appearance.font_family', label='Typeface', group='Appearance',
        type='enum', default='default',
        choices=(('default', 'Inter (default)'), ('system', 'System UI'),
                 ('serif', 'Serif'), ('mono', 'Monospace'),
                 ('rounded', 'Rounded')),
        help='Font used for body text.',
        keywords=('font', 'typeface', 'serif', 'monospace', 'font family'),
    ),
    Setting(
        key='appearance.corner_style', label='Corner style', group='Appearance',
        type='enum', default='rounded',
        choices=(('rounded', 'Rounded'), ('soft', 'Slightly rounded'), ('square', 'Square')),
        help='Corner radius on cards and buttons.',
        keywords=('corners', 'rounded', 'square corners', 'border radius'),
    ),
    Setting(
        key='appearance.background', label='Page background', group='Appearance',
        type='enum', default='plain',
        choices=(('plain', 'Plain'), ('gradient', 'Soft gradient'),
                 ('grid', 'Subtle grid'), ('dots', 'Dot grid')),
        help='Texture behind the content.',
        keywords=('background', 'wallpaper', 'gradient', 'texture', 'pattern'),
    ),
    Setting(
        key='appearance.reduce_motion', label='Reduce motion', group='Appearance', tier='simple',
        type='bool', default=False, intent_inverted=True,
        help='Turn off slide and fade animations.',
        keywords=('animation', 'animations', 'motion', 'reduce motion',
                  'stop moving', 'no animation', 'accessibility motion'),
    ),
    Setting(
        key='appearance.custom_css', label='Custom CSS', group='Appearance', tier='expert',
        type='text', default='', maximum=4000,
        help='Injected as a stylesheet on every page. Yours alone — it never leaves your account.',
        keywords=('custom css', 'stylesheet', 'own styles', 'css override'),
    ),

    # ── Interface ────────────────────────────────────────────────────
    Setting(
        key='ui.mode', label='Interface depth', group='Interface', tier='simple',
        type='enum', default='simple',
        choices=(('simple', 'Simple — just the essentials'),
                 ('advanced', 'Advanced — more controls'),
                 ('expert', 'Everything — nothing hidden')),
        help='Controls how many options the app puts in front of you. '
             'Search on this page always covers every option regardless.',
        keywords=('simple mode', 'advanced mode', 'expert mode', 'show everything',
                  'hide options', 'beginner mode', 'power user', 'interface depth'),
    ),
    Setting(
        key='ui.landing_page', label='Open on', group='Interface',
        type='enum', default='dashboard',
        choices=(('dashboard', 'Dashboard'), ('planner', 'Planner'),
                 ('library', 'Library'), ('progress', 'Progress'),
                 ('session', 'Session')),
        help='Page the app opens to.',
        keywords=('landing page', 'home page', 'start page', 'open on',
                  'default page', 'first page'),
    ),
    Setting(
        key='ui.nav_style', label='Navigation style', group='Interface',
        type='enum', default='full',
        choices=(('full', 'Labels'), ('compact', 'Short labels'), ('icons', 'Icons only')),
        help='How the top navigation bar is drawn.',
        keywords=('navigation', 'nav bar', 'menu', 'icons only', 'nav labels'),
    ),
    Setting(
        key='ui.command_palette', label='Quick command bar', group='Interface',
        type='bool', default=True,
        help='Press Ctrl+K (or Cmd+K) anywhere to jump to a page or change a setting.',
        keywords=('command palette', 'quick bar', 'ctrl k', 'cmd k', 'shortcut bar',
                  'search bar', 'quick switcher'),
    ),
    Setting(
        key='ui.show_tips', label='Inline tips', group='Interface', tier='simple',
        type='bool', default=True,
        help='Short explanations under controls. Turn off once you know your way around.',
        keywords=('tips', 'hints', 'help text', 'explanations', 'descriptions'),
    ),
    Setting(
        key='ui.confirm_destructive', label='Confirm before discarding', group='Interface',
        type='bool', default=True,
        help='Ask before stopping a session or clearing data.',
        keywords=('confirm', 'are you sure', 'confirmation', 'warn me', 'prompt'),
    ),

    # ── Dashboard ────────────────────────────────────────────────────
    Setting(
        key='dashboard.cards', label='Dashboard sections', group='Dashboard', tier='simple',
        type='multi',
        default=['today', 'quick_actions', 'goals', 'recent'],
        choices=(('today', "Today's session"), ('quick_actions', 'Quick actions'),
                 ('goals', 'Weekly goal'), ('saved', 'Saved templates'),
                 ('recent', 'Recent activity'), ('heatmap', 'Activity heatmap')),
        help='Which blocks appear on the dashboard, and nothing else.',
        keywords=('dashboard', 'home cards', 'sections', 'widgets', 'blocks',
                  'hide recent', 'show heatmap', 'show saved'),
    ),
    Setting(
        key='dashboard.kpis', label='Headline numbers', group='Dashboard',
        type='multi',
        default=['exercises', 'avg_time', 'streak', 'sessions'],
        choices=(('exercises', 'Exercises completed'), ('avg_time', 'Average session'),
                 ('streak', 'Day streak'), ('sessions', 'Total sessions'),
                 ('minutes', 'Total minutes'), ('goal', 'Goal progress')),
        help='The stat tiles across the top of the dashboard.',
        keywords=('kpi', 'stats', 'headline numbers', 'stat cards', 'metrics',
                  'top numbers'),
    ),
    Setting(
        key='dashboard.greeting', label='Greeting', group='Dashboard',
        type='enum', default='name',
        choices=(('name', 'Welcome back, <name>'), ('time', 'Good morning / afternoon'),
                 ('none', 'No greeting')),
        help='The line above the dashboard heading.',
        keywords=('greeting', 'welcome message', 'hello', 'good morning'),
    ),

    # ── Workout building ─────────────────────────────────────────────
    Setting(
        key='workout.default_duration', label='Default length', group='Workout building', tier='simple',
        type='int', default=20, minimum=5, maximum=180, step=5, unit=' min',
        help='Where the planner slider starts.',
        keywords=('default duration', 'workout length', 'how long', 'default length',
                  'session length', 'minutes'),
    ),
    Setting(
        key='workout.default_difficulty', label='Default difficulty', group='Workout building', tier='simple',
        type='enum', default='beginner',
        choices=(('beginner', 'Beginner'), ('intermediate', 'Intermediate'),
                 ('advanced', 'Advanced')),
        help='Difficulty the planner preselects.',
        keywords=('difficulty', 'level', 'beginner', 'intermediate', 'advanced',
                  'harder', 'easier', 'intensity'),
    ),
    Setting(
        key='workout.default_domains', label='Default focus areas', group='Workout building', tier='simple',
        type='multi', default=['Strength', 'Endurance'],
        choices=DOMAIN_CHOICES,
        help='Domains preselected in the planner.',
        keywords=('domains', 'focus areas', 'training types', 'categories',
                  'strength', 'cardio', 'mobility', 'agility', 'cognitive'),
    ),
    Setting(
        key='workout.equipment', label='Equipment I have', group='Workout building', tier='simple',
        type='multi', default=list(EQUIPMENT_DEFAULT),
        choices=EQUIPMENT_CHOICES,
        help='Exercises needing anything not listed here are left out of generated workouts.',
        keywords=('equipment', 'gear', 'dumbbells', 'no equipment', 'bodyweight only',
                  'resistance band', 'pull up bar', 'kettlebell', 'what i have'),
    ),
    Setting(
        key='workout.exclude_keywords', label='Never include', group='Workout building',
        type='text', default='', maximum=400,
        help='Comma-separated words. Any exercise whose name, muscles or description '
             'matches one is skipped — useful for injuries ("knee, jump").',
        keywords=('exclude', 'avoid', 'skip exercises', 'injury', 'no jumping',
                  'blacklist', 'ban', 'leave out', 'never include'),
    ),
    Setting(
        key='workout.include_warmup', label='Add a warm-up', group='Workout building', tier='simple',
        type='bool', default=False,
        help='Prepends mobility work before the main block.',
        keywords=('warm up', 'warmup', 'warm-up', 'stretching first', 'prepare'),
    ),
    Setting(
        key='workout.warmup_minutes', label='Warm-up length', group='Workout building',
        type='int', default=3, minimum=1, maximum=20, step=1, unit=' min',
        help='Only used when a warm-up is added.',
        keywords=('warm up length', 'warmup minutes', 'warm up duration'),
    ),
    Setting(
        key='workout.include_cooldown', label='Add a cool-down', group='Workout building', tier='simple',
        type='bool', default=False,
        help='Appends easy mobility work at the end.',
        keywords=('cool down', 'cooldown', 'cool-down', 'stretch after', 'finish easy'),
    ),
    Setting(
        key='workout.cooldown_minutes', label='Cool-down length', group='Workout building',
        type='int', default=3, minimum=1, maximum=20, step=1, unit=' min',
        help='Only used when a cool-down is added.',
        keywords=('cool down length', 'cooldown minutes', 'cool down duration'),
    ),
    Setting(
        key='workout.ordering', label='Exercise order', group='Workout building',
        type='enum', default='alternate',
        choices=(('as_generated', 'As chosen'), ('alternate', 'Alternate focus areas'),
                 ('hardest_first', 'Hardest first'), ('easiest_first', 'Easiest first'),
                 ('longest_first', 'Longest first'), ('shortest_first', 'Shortest first')),
        help='How the exercises are sequenced once picked.',
        keywords=('order', 'ordering', 'sequence', 'sort exercises', 'hardest first',
                  'alternate', 'shuffle'),
    ),
    Setting(
        key='workout.variety', label='Repeat handling', group='Workout building',
        type='enum', default='balanced',
        choices=(('repeat_ok', 'Repeats are fine'),
                 ('balanced', 'Prefer something new'),
                 ('strict', 'Never repeat recent exercises')),
        help='How hard the generator works to avoid exercises you did recently.',
        keywords=('variety', 'repeats', 'same exercises', 'mix it up', 'rotation',
                  'something new', 'boring'),
    ),
    Setting(
        key='workout.difficulty_spillover', label='Relax difficulty when short', group='Workout building',
        type='bool', default=True,
        help='If too few exercises match your level, borrow from neighbouring levels '
             'rather than returning a thin workout.',
        keywords=('spillover', 'relax difficulty', 'other levels', 'not enough exercises'),
    ),
    Setting(
        key='workout.min_exercise_minutes', label='Shortest block', group='Workout building', tier='expert',
        type='float', default=0.5, minimum=0.25, maximum=10, step=0.25, unit=' min',
        help='Floor for any single exercise.',
        keywords=('minimum duration', 'shortest exercise', 'min block'),
    ),
    Setting(
        key='workout.max_exercise_minutes', label='Longest block', group='Workout building', tier='expert',
        type='float', default=15.0, minimum=1, maximum=60, step=0.5, unit=' min',
        help='Ceiling for any single exercise.',
        keywords=('maximum duration', 'longest exercise', 'max block', 'cap'),
    ),
    Setting(
        key='workout.duration_tolerance', label='Length tolerance', group='Workout building', tier='expert',
        type='int', default=40, minimum=5, maximum=75, step=5, unit='%',
        help='How far a generated workout may drift from the target length before '
             'it is rejected and rebuilt.',
        keywords=('tolerance', 'accuracy', 'close to target', 'duration tolerance'),
    ),

    # ── Timers & rest ────────────────────────────────────────────────
    Setting(
        key='timers.rest_source', label='Rest between exercises', group='Timers & rest', tier='simple',
        type='enum', default='auto',
        choices=(('auto', 'Automatic (by domain and level)'),
                 ('fixed', 'Same every time'), ('none', 'No rest')),
        help='Automatic uses the published rest table for the domain and difficulty.',
        keywords=('rest', 'rest time', 'break', 'pause between', 'recovery',
                  'no rest', 'rest between exercises'),
    ),
    Setting(
        key='timers.fixed_rest_seconds', label='Fixed rest length', group='Timers & rest',
        type='int', default=30, minimum=0, maximum=300, step=5, unit='s',
        help='Used when rest is set to "Same every time".',
        keywords=('rest seconds', 'rest length', 'break length', 'fixed rest',
                  '30 seconds rest', 'longer rest', 'shorter rest'),
    ),
    Setting(
        key='timers.rest_multiplier', label='Rest scaling', group='Timers & rest', tier='expert',
        type='int', default=100, minimum=25, maximum=200, step=5, unit='%',
        help='Scales automatic rest times up or down across the board.',
        keywords=('rest multiplier', 'scale rest', 'more rest', 'less rest',
                  'rest percentage'),
    ),
    Setting(
        key='timers.prep_seconds', label='Get-ready countdown', group='Timers & rest', tier='simple',
        type='int', default=5, minimum=0, maximum=30, step=1, unit='s',
        help='Counts down before each exercise starts. Zero starts immediately.',
        keywords=('countdown', 'get ready', 'prep time', 'three two one',
                  'before starting', 'lead in'),
    ),
    Setting(
        key='timers.auto_advance', label='Advance automatically', group='Timers & rest', tier='simple',
        type='bool', default=True,
        help='Move to the next exercise when the timer ends. Off means you tap to continue.',
        keywords=('auto advance', 'next automatically', 'move on', 'auto next',
                  'wait for me', 'manual advance'),
    ),
    Setting(
        key='timers.warning_seconds', label='Final warning', group='Timers & rest',
        type='int', default=3, minimum=0, maximum=15, step=1, unit='s',
        help='The timer turns red and ticks for the last few seconds. Zero disables it.',
        keywords=('warning', 'last seconds', 'final countdown', 'red timer', 'about to end'),
    ),
    Setting(
        key='timers.display', label='Timer shows', group='Timers & rest',
        type='enum', default='remaining',
        choices=(('remaining', 'Time remaining'), ('elapsed', 'Time elapsed'),
                 ('both', 'Both')),
        help='What the number in the ring counts.',
        keywords=('timer display', 'count up', 'count down', 'elapsed', 'remaining'),
    ),
    Setting(
        key='timers.big_timer', label='Oversized timer', group='Timers & rest',
        type='bool', default=False,
        help='Much larger numerals — readable from across the room.',
        keywords=('big timer', 'large timer', 'huge numbers', 'across the room',
                  'bigger clock'),
    ),

    # ── Sound & feedback ─────────────────────────────────────────────
    Setting(
        key='audio.enabled', label='Sound cues', group='Sound & feedback', tier='simple',
        type='bool', default=True,
        help='Plays a tone when an exercise or rest period ends.',
        keywords=('sound', 'audio', 'beep', 'noise', 'mute', 'silent', 'quiet',
                  'sound effects', 'tone', 'no sound', 'no audio', 'no beep'),
    ),
    Setting(
        key='audio.volume', label='Cue volume', group='Sound & feedback',
        type='int', default=60, minimum=0, maximum=100, step=5, unit='%',
        help='Volume of the generated tones.',
        keywords=('volume', 'louder', 'quieter', 'sound level', 'turn it up',
                  'turn it down'),
    ),
    Setting(
        key='audio.cue_style', label='Cue sound', group='Sound & feedback',
        type='enum', default='beep',
        choices=(('beep', 'Beep'), ('chime', 'Chime'), ('click', 'Click'),
                 ('bell', 'Bell')),
        help='Character of the tone played at transitions.',
        keywords=('cue sound', 'beep style', 'chime', 'bell', 'click', 'tone style'),
    ),
    Setting(
        key='audio.countdown_ticks', label='Tick on final seconds', group='Sound & feedback',
        type='bool', default=True,
        help='A short tick for each of the last few seconds.',
        keywords=('ticks', 'ticking', 'countdown sound', 'tick tock'),
    ),
    Setting(
        key='audio.voice_announce', label='Announce exercise names', group='Sound & feedback',
        type='bool', default=False,
        help='Speaks the next exercise aloud using your browser voice — handy when '
             'the screen is out of reach.',
        keywords=('voice', 'speak', 'say the name', 'read out', 'announce',
                  'text to speech', 'talk'),
    ),
    Setting(
        key='audio.vibrate', label='Vibrate on transitions', group='Sound & feedback',
        type='bool', default=True,
        help='Phone haptics at the end of each block. Ignored on desktop.',
        keywords=('vibrate', 'vibrating', 'vibration', 'haptics', 'buzz',
                  'phone vibration'),
    ),

    # ── During a session ─────────────────────────────────────────────
    Setting(
        key='session.tracking_mode', label='What I log', group='During a session', tier='simple',
        type='enum', default='timer',
        choices=(('timer', 'Just time — run the clock'),
                 ('reps', 'Reps'),
                 ('sets_reps', 'Sets and reps'),
                 ('sets_reps_weight', 'Sets, reps and weight')),
        help='Anything beyond "just time" adds a small logging panel to each exercise '
             'and records your numbers against it.',
        keywords=('sets', 'reps', 'weight', 'track weight', 'log reps', 'strength log',
                  'lifting', 'record weight', 'tracking mode', 'kilos', 'pounds'),
    ),
    Setting(
        key='session.default_sets', label='Default sets', group='During a session',
        type='int', default=3, minimum=1, maximum=15, step=1,
        help='Prefilled when logging sets.',
        keywords=('default sets', 'how many sets', 'number of sets'),
    ),
    Setting(
        key='session.default_reps', label='Default reps', group='During a session',
        type='int', default=10, minimum=1, maximum=100, step=1,
        help='Prefilled when logging reps.',
        keywords=('default reps', 'how many reps', 'number of reps'),
    ),
    Setting(
        key='session.show_instructions', label='Show instructions', group='During a session', tier='simple',
        type='bool', default=True,
        help='The how-to text under the timer.',
        keywords=('instructions', 'how to', 'guidance', 'steps', 'description'),
    ),
    Setting(
        key='session.show_next_up', label='Show what is next', group='During a session',
        type='bool', default=True,
        help='Names the upcoming exercise during rest.',
        keywords=('next up', 'coming next', 'preview', 'whats next'),
    ),
    Setting(
        key='session.show_sidebar', label='Show exercise list', group='During a session',
        type='bool', default=True,
        help='The running checklist beside the timer.',
        keywords=('sidebar', 'exercise list', 'checklist', 'side panel'),
    ),
    Setting(
        key='session.keep_awake', label='Keep the screen on', group='During a session', tier='simple',
        type='bool', default=True,
        help='Holds a wake lock so the display does not sleep mid-set.',
        keywords=('screen off', 'keep awake', 'wake lock', 'screen timeout',
                  'stay on', 'screen sleep', 'dimming'),
    ),
    Setting(
        key='session.autosave', label='Save without asking', group='During a session', tier='simple',
        type='bool', default=False,
        help='Records the session the moment it finishes instead of waiting for '
             'a tap on Save.',
        keywords=('autosave', 'save automatically', 'auto save', 'dont ask to save'),
    ),
    Setting(
        key='session.allow_skip', label='Allow skipping', group='During a session',
        type='bool', default=True,
        help='Shows the Skip button on exercises and rest.',
        keywords=('skip', 'skip button', 'jump ahead', 'no skipping'),
    ),
    Setting(
        key='session.rpe_prompt', label='Ask how hard it felt', group='During a session',
        type='bool', default=False,
        help='A 1–10 effort rating at the end of each session, saved with it.',
        keywords=('rpe', 'effort', 'how hard', 'difficulty rating', 'perceived exertion'),
    ),

    # ── Units & formats ──────────────────────────────────────────────
    Setting(
        key='units.weight', label='Weight unit', group='Units & formats', tier='simple',
        type='enum', default='kg',
        choices=(('kg', 'Kilograms'), ('lb', 'Pounds')),
        help='Used wherever weight is logged or shown.',
        keywords=('kg', 'lb', 'pounds', 'kilograms', 'kilos', 'weight unit',
                  'metric', 'imperial'),
    ),
    Setting(
        key='units.distance', label='Distance unit', group='Units & formats',
        type='enum', default='km',
        choices=(('km', 'Kilometres'), ('mi', 'Miles')),
        help='Used wherever distance is shown.',
        keywords=('km', 'miles', 'distance unit', 'kilometres', 'metric', 'imperial'),
    ),
    Setting(
        key='units.time_format', label='Clock format', group='Units & formats',
        type='enum', default='24h',
        choices=(('24h', '24-hour'), ('12h', '12-hour')),
        help='How times of day are written.',
        keywords=('12 hour', '24 hour', 'am pm', 'clock format', 'time format'),
    ),
    Setting(
        key='units.date_format', label='Date format', group='Units & formats',
        type='enum', default='iso',
        choices=(('iso', '2026-08-22'), ('eu', '22/08/2026'),
                 ('us', '08/22/2026'), ('long', '22 August 2026')),
        help='How dates are written throughout the app.',
        keywords=('date format', 'day month year', 'month day year', 'dates'),
    ),
    Setting(
        key='units.week_start', label='Week starts on', group='Units & formats',
        type='enum', default='monday',
        choices=(('monday', 'Monday'), ('sunday', 'Sunday'), ('saturday', 'Saturday')),
        help='Affects the activity heatmap and weekly totals.',
        # Deliberately no weekday-name keywords: the choice needles already
        # match those, and doubling up made any sentence mentioning a weekday
        # look like a request to move the week start.
        keywords=('week start', 'first day of week', 'start my week', 'calendar'),
    ),

    # ── Goals & habits ───────────────────────────────────────────────
    Setting(
        key='goals.weekly_sessions', label='Sessions per week', group='Goals & habits', tier='simple',
        type='int', default=4, minimum=0, maximum=21, step=1,
        help='Your target. Zero hides the goal display entirely.',
        keywords=('weekly goal', 'sessions per week', 'target', 'how often',
                  'times a week', 'goal'),
    ),
    Setting(
        key='goals.weekly_minutes', label='Minutes per week', group='Goals & habits',
        type='int', default=120, minimum=0, maximum=2000, step=10, unit=' min',
        help='Secondary target shown alongside sessions. Zero hides it.',
        keywords=('weekly minutes', 'minutes target', 'time goal', 'training volume'),
    ),
    Setting(
        key='goals.rest_days', label='Planned rest days', group='Goals & habits',
        type='multi', default=[], choices=WEEKDAYS,
        help='The dashboard suggests recovery instead of a session on these days, '
             'and they never break your streak.',
        keywords=('rest day', 'rest days', 'day off', 'no training', 'recovery day',
                  'sunday off', 'weekend off'),
    ),
    Setting(
        key='goals.streak_grace_days', label='Streak forgiveness', group='Goals & habits',
        type='int', default=0, minimum=0, maximum=3, step=1, unit=' day(s)',
        help='Missed days your streak survives. One means a single skipped day '
             'does not reset you to zero.',
        keywords=('streak', 'grace', 'forgiveness', 'missed day', 'dont lose streak',
                  'streak protection'),
    ),

    # ── Progress & charts ────────────────────────────────────────────
    Setting(
        key='progress.charts', label='Charts shown', group='Progress & charts', tier='simple',
        type='multi', default=['domain', 'weekly'],
        choices=(('domain', 'Domain distribution'), ('weekly', 'Weekly activity'),
                 ('heatmap', 'Activity heatmap'), ('trend', 'Session length trend')),
        help='Charts on the progress page. Fewer charts means a faster page.',
        keywords=('charts', 'graphs', 'plots', 'visualisations', 'hide charts',
                  'progress charts'),
    ),
    Setting(
        key='progress.chart_style', label='Chart style', group='Progress & charts',
        type='enum', default='bar',
        choices=(('bar', 'Bars'), ('line', 'Lines'), ('area', 'Filled area')),
        help='How the weekly activity chart is drawn.',
        keywords=('chart style', 'bar chart', 'line chart', 'graph type'),
    ),
    Setting(
        key='progress.history_limit', label='History length', group='Progress & charts',
        type='int', default=20, minimum=5, maximum=200, step=5, unit=' sessions',
        help='How many past sessions the history list shows.',
        keywords=('history', 'past sessions', 'how many sessions', 'log length',
                  'recent activity length'),
    ),

    # ── Local AI assistant ───────────────────────────────────────────
    Setting(
        key='ai.enabled', label='Use my local AI model', group='Local AI', tier='simple',
        type='bool', default=True,
        help='When off, the app never contacts a model and uses its built-in rules '
             'for everything. Nothing breaks — it just gets more predictable.',
        keywords=('ai', 'local ai', 'llm', 'model', 'lm studio', 'turn off ai',
                  'disable ai', 'offline'),
    ),
    Setting(
        key='ai.workout_generation', label='Let it build workouts', group='Local AI', tier='simple',
        type='bool', default=False,
        help='Off by default: benchmarked against the rule-based planner, a small '
             'local model was slower and no more accurate. Turn it on if you want '
             'your written goal taken into account; every pick is still checked '
             'against the database before you see it.',
        keywords=('ai workouts', 'generate workout', 'ai planning', 'model picks'),
    ),
    Setting(
        key='ai.allow_settings_changes', label='Let it change settings', group='Local AI', tier='simple',
        type='bool', default=True,
        help='Enables the "just ask" box — describe what you want and it finds the '
             'right switches.',
        keywords=('ai settings', 'change settings', 'ask ai', 'voice control',
                  'natural language', 'just ask'),
    ),
    Setting(
        key='ai.auto_apply', label='Apply without confirming', group='Local AI',
        type='bool', default=False,
        help='Skips the review step when the assistant is confident. Everything it '
             'does is still undoable from this page.',
        keywords=('auto apply', 'confirm changes', 'without asking', 'just do it',
                  'no confirmation'),
    ),
    Setting(
        key='ai.server_url', label='Model server address', group='Local AI', tier='expert',
        type='text', default='', maximum=200,
        help='OpenAI-compatible endpoint. Blank uses the app default '
             '(LM Studio on localhost:1234).',
        keywords=('server url', 'endpoint', 'api url', 'lm studio address',
                  'ollama', 'localhost', 'port'),
    ),
    Setting(
        key='ai.model', label='Model name', group='Local AI', tier='expert',
        type='text', default='', maximum=120,
        help='Blank asks the server which model is loaded.',
        keywords=('model name', 'which model', 'model id', 'llama', 'qwen', 'mistral'),
    ),
    Setting(
        key='ai.timeout_seconds', label='Give up after', group='Local AI', tier='expert',
        type='int', default=10, minimum=3, maximum=120, step=1, unit='s',
        help='How long to wait for the model before falling back to built-in rules. '
             'Raise it for large models on slow hardware.',
        keywords=('timeout', 'too slow', 'wait longer', 'give up', 'response time'),
    ),
    Setting(
        key='ai.fallback_notice', label='Tell me when it falls back', group='Local AI',
        type='bool', default=True,
        help='A small notice when the rule-based engine handled something instead '
             'of the model, so you always know which one answered.',
        keywords=('fallback', 'notify', 'which engine', 'rule based', 'notice'),
    ),

    # ── Data ─────────────────────────────────────────────────────────
    Setting(
        key='data.export_format', label='Export format', group='Data', tier='simple',
        type='enum', default='json',
        choices=(('json', 'JSON — complete'), ('csv', 'CSV — spreadsheet friendly')),
        help='Format used by the export button below.',
        keywords=('export', 'download', 'backup', 'json', 'csv', 'spreadsheet',
                  'my data', 'take my data'),
    ),
    Setting(
        key='data.history_retention_days', label='Keep history for', group='Data',
        type='int', default=0, minimum=0, maximum=3650, step=30, unit=' days',
        help='Sessions older than this are hidden from views. Zero keeps everything '
             'forever. Nothing is deleted either way.',
        keywords=('retention', 'keep history', 'delete old', 'how long', 'old sessions'),
    ),

    # ── Advanced ─────────────────────────────────────────────────────
    Setting(
        key='advanced.developer_mode', label='Developer mode', group='Advanced', tier='expert',
        type='bool', default=False,
        help='Shows raw payloads, timings and the engine that answered each request.',
        keywords=('developer', 'debug', 'raw data', 'diagnostics', 'verbose',
                  'behind the scenes'),
    ),
    Setting(
        key='advanced.offline_cache', label='Work offline', group='Advanced',
        type='bool', default=True,
        help='Caches pages and assets so the app loads instantly and keeps working '
             'without a network.',
        keywords=('offline', 'service worker', 'cache', 'no internet', 'pwa',
                  'install', 'faster loading'),
    ),
]


SETTINGS_BY_KEY = {s.key: s for s in SETTINGS}

GROUP_ORDER = []
for _s in SETTINGS:
    if _s.group not in GROUP_ORDER:
        GROUP_ORDER.append(_s.group)

GROUP_ICONS = {
    'Appearance': 'palette',
    'Interface': 'layout-dashboard',
    'Dashboard': 'gauge',
    'Workout building': 'dumbbell',
    'Timers & rest': 'timer',
    'Sound & feedback': 'volume-2',
    'During a session': 'activity',
    'Units & formats': 'ruler',
    'Goals & habits': 'target',
    'Progress & charts': 'trending-up',
    'Local AI': 'sparkles',
    'Data': 'database',
    'Advanced': 'settings-2',
}

TIER_RANK = {'simple': 0, 'advanced': 1, 'expert': 2}


def defaults():
    """A fresh settings dict. Lists are copied so callers cannot mutate the registry."""
    return {s.key: (list(s.default) if isinstance(s.default, list) else s.default)
            for s in SETTINGS}


def merge(stored):
    """Overlay stored values on the defaults, dropping keys that no longer exist.

    Retired settings vanish rather than lingering as dead keys, and settings
    added since the user last saved pick up their default.
    """
    result = defaults()
    for key, value in (stored or {}).items():
        setting = SETTINGS_BY_KEY.get(key)
        if not setting:
            continue
        ok, coerced, _ = coerce(setting, value)
        if ok:
            result[key] = coerced
    return result


# ── Validation and coercion ──────────────────────────────────────────

def coerce(setting, value):
    """Convert an arbitrary incoming value to this setting's storage type.

    Returns ``(ok, value, error)``. The error string is written for a person —
    it is shown in the UI and fed back to the model on a corrective retry — so
    it names what was expected rather than describing a type failure.
    """
    if setting.type == 'bool':
        if isinstance(value, bool):
            return True, value, None
        token = str(value).strip().lower()
        if token in _TRUE_WORDS:
            return True, True, None
        if token in _FALSE_WORDS:
            return True, False, None
        return False, None, f'"{setting.label}" is a switch — use on or off.'

    if setting.type == 'enum':
        return _coerce_choice(setting, value)

    if setting.type == 'multi':
        if isinstance(value, str):
            items = [p.strip() for p in value.split(',') if p.strip()]
        elif isinstance(value, (list, tuple)):
            items = list(value)
        else:
            return False, None, f'"{setting.label}" expects a list of options.'
        resolved, seen = [], set()
        for item in items:
            ok, val, err = _coerce_choice(setting, item)
            if not ok:
                return False, None, err
            if val not in seen:
                seen.add(val)
                resolved.append(val)
        return True, resolved, None

    if setting.type in ('int', 'float'):
        try:
            number = float(str(value).strip().rstrip('%').strip())
        except (TypeError, ValueError):
            return False, None, f'"{setting.label}" expects a number.'
        if setting.minimum is not None:
            number = max(number, setting.minimum)
        if setting.maximum is not None:
            number = min(number, setting.maximum)
        return True, (int(round(number)) if setting.type == 'int' else round(number, 2)), None

    # text. Angle brackets are stripped from every text setting: none of them
    # needs markup, and one of them (appearance.custom_css) is rendered inside
    # a <style> element, where a stray `</style>` would end the block early.
    # Sanitising at the boundary means the template can render it plainly.
    text = '' if value is None else str(value)
    text = text.replace('<', '').replace('>', '')
    limit = int(setting.maximum) if setting.maximum else 2000
    return True, text[:limit], None


def _coerce_choice(setting, value):
    """Resolve one value against a setting's choices, forgivingly.

    Accepts the stored value, the displayed label, or a distinctive word from
    the label, so both a UI post ("dark") and a model's guess ("Dark") land on
    the same stored value.
    """
    token = str(value).strip()
    lowered = token.lower()
    for val, _label in setting.choices:
        if token == val:
            return True, val, None
    for val, _label in setting.choices:
        if lowered == str(val).lower():
            return True, val, None
    for val, label in setting.choices:
        if lowered == label.lower():
            return True, val, None
    for val, label in setting.choices:
        first = label.lower().split('—')[0].strip()
        if lowered == first or (len(lowered) > 2 and lowered in first):
            return True, val, None
    for val, _label in setting.choices:
        if lowered in CHOICE_ALIASES.get(str(val), ()):
            return True, val, None
    allowed = ', '.join(v for v, _ in setting.choices)
    return False, None, f'"{setting.label}" must be one of: {allowed}.'


def apply_changes(current, changes):
    """Validate a batch of ``{key, value}`` changes against the registry.

    Returns ``(accepted, rejected)``. ``accepted`` holds one entry per change
    that both resolved and actually differs from the current value — a request
    to turn on something already on produces no entry, which is what keeps the
    assistant from reporting work it did not do.
    """
    accepted, rejected = [], []
    for change in changes or []:
        if not isinstance(change, dict):
            rejected.append({'key': None, 'error': 'Each change must name a setting and a value.'})
            continue
        key = str(change.get('key', '')).strip()
        setting = SETTINGS_BY_KEY.get(key)
        if not setting:
            rejected.append({'key': key, 'error': f'"{key}" is not a setting in this app.'})
            continue
        ok, value, error = coerce(setting, change.get('value'))
        if not ok:
            rejected.append({'key': key, 'error': error})
            continue
        before = current.get(key, setting.default)
        if before == value:
            continue
        accepted.append({
            'key': key,
            'label': setting.label,
            'group': setting.group,
            'value': value,
            'previous': before,
            'value_label': setting.label_for(value),
            'previous_label': setting.label_for(before),
        })
    return accepted, rejected


def describe_changes(accepted, applied=True):
    """Sentence describing a set of changes, built from registry labels only.

    The language model never writes this. It selects keys and values; every
    word a user reads here comes from the registry and from values that passed
    validation. Same contract as ``build_workout_explanation`` in ``app.py``.
    See ``learn/07-truth-boundary.md``.

    ``applied=False`` phrases the same changes as a proposal, for the review
    step shown before anything is committed.
    """
    if not accepted:
        return 'Nothing needed changing — those settings were already set that way.'
    parts = []
    for change in accepted:
        setting = SETTINGS_BY_KEY.get(change['key'])
        if setting is not None and setting.type == 'bool':
            verb = 'turned' if applied else 'turn'
            parts.append(f"{verb} {change['value_label']} {setting.label.lower()}")
        else:
            verb = 'set' if applied else 'set'
            parts.append(f"{verb} {change['label'].lower()} to {change['value_label']}")
    if len(parts) == 1:
        sentence = parts[0]
    else:
        sentence = ', '.join(parts[:-1]) + f' and {parts[-1]}'
    sentence = sentence[0].upper() + sentence[1:] + '.'
    return sentence if applied else f'This will {sentence[0].lower()}{sentence[1:]}'


# ── Deterministic natural-language matching ──────────────────────────
#
# This runs before the model and again if the model is unavailable, so the
# "just ask" box works on a machine with no LLM at all. Anything it resolves
# confidently never reaches the model, which also makes the common cases
# instant.

_NEGATIVE_HINTS = ('turn off', 'switch off', 'disable', 'stop', 'no ', 'not ',
                   'never', 'hide', 'remove', 'without', 'dont', "don't",
                   'mute', 'silence', 'silent', 'quiet', 'less', 'off',
                   'smaller', 'shorter', 'lower', 'reduce', 'decrease', 'down',
                   'hate', 'annoying', 'sick of', 'tired of', 'get rid of')
_POSITIVE_HINTS = ('turn on', 'switch on', 'enable', 'show', 'add', 'want',
                   'give me', 'always', 'let me', 'allow', 'more', 'on',
                   'bigger', 'larger', 'longer', 'higher', 'raise', 'increase',
                   'up', 'louder')

# Words that carry no signal about which setting is meant.
_STOPWORDS = {
    'the', 'a', 'an', 'to', 'for', 'of', 'my', 'me', 'i', 'it', 'is', 'be',
    'can', 'you', 'please', 'make', 'set', 'change', 'want', 'would', 'like',
    'and', 'or', 'in', 'on', 'at', 'with', 'this', 'that', 'app', 'thing',
    'stuff', 'should', 'could', 'have', 'get', 'put', 'do', 'does', 'just',
    'really', 'very', 'so', 'too', 'am', 'are', 'was', 'all', 'up', 'down',
}


def _normalise(text):
    cleaned = re.sub(r'[^a-z0-9%\s\.]', ' ', str(text or '').lower())
    return re.sub(r'\s+', ' ', cleaned).strip()


def _tokens(text):
    return [t for t in _normalise(text).split() if t and t not in _STOPWORDS]


def _stem(word):
    """Light suffix stripping so "beeping" finds the "beep" keyword.

    Deliberately crude — a real stemmer is not worth a dependency for keyword
    matching, and over-stemming would create false hits.
    """
    for suffix in ('ing', 'ed', 'es', 's'):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[:len(word) - len(suffix)]
    return word


def _stems(words):
    return {_stem(w) for w in words}


def score_setting(setting, query):
    """How strongly a query points at one setting. Higher is better; 0 is no match.

    Three strengths of evidence, deliberately weighted apart:

    * an exact phrase hit ("dark mode") is worth most, because a phrase
      identifies a setting far more precisely than its words do separately;
    * every word of a multi-word keyword appearing *somewhere* is worth less
      but still counts, so "make the text bigger" reaches the "bigger text"
      keyword that a strict phrase match would miss;
    * a single keyword word, or a word from the label, is weakest.
    """
    normalised = _normalise(query)
    padded = f' {normalised} '
    query_tokens = set(_tokens(query))
    query_stems = _stems(normalised.split())
    score = 0.0

    for keyword in setting.keywords:
        key_norm = _normalise(keyword).strip()
        if not key_norm:
            continue
        key_words = key_norm.split()
        if f' {key_norm} ' in padded:
            score += 6.0 * len(key_words)
        elif len(key_words) > 1 and all(_stem(word) in query_stems for word in key_words):
            # Same words in another order, inflected, or with filler between.
            score += 3.0 * len(key_words)
        elif len(key_words) == 1 and _stem(key_norm) in query_stems:
            score += 3.0

    label_tokens = set(_tokens(setting.label))
    if label_tokens:
        overlap = len(_stems(label_tokens) & _stems(query_tokens))
        if overlap == len(label_tokens):
            # The whole label was said — near-certain reference.
            score += 3.5 * overlap + 3.0
        elif overlap:
            # A stray shared word ("mode", "use") is barely evidence at all;
            # weighting it low keeps one-word overlaps from proposing settings
            # the request never meant.
            score += 1.5 * overlap

    if set(_tokens(setting.group)) & query_tokens:
        score += 1.0

    for candidate, _value in _choice_needles(setting):
        if len(candidate) > 2 and candidate not in _GENERIC_CHOICE_WORDS                 and f' {candidate} ' in padded:
            score += 4.0
            break

    return score


# Choice spellings that are ordinary English words. They still resolve a value
# once a setting is chosen, but they are not allowed to *select* the setting —
# otherwise "make everything purple" would flip on expert mode because one of
# the interface-depth choices happens to be called "everything".
_GENERIC_CHOICE_WORDS = {
    'everything', 'none', 'all', 'both', 'default', 'system', 'auto', 'fixed',
    'plain', 'name', 'nothing at all', 'no greeting', 'lines', 'bars',
}


# Everyday words for stored choice values that neither the value nor its label
# contains. Applied wherever choices are matched, including validation of the
# model's output, so "purple" resolves to violet on every path.
CHOICE_ALIASES = {
    'violet': ('purple',),
    'rose': ('pink',),
    'amber': ('orange', 'yellow'),
    'lime': ('green',),
    'slate': ('grey', 'gray'),
    'teal': ('turquoise', 'cyan'),
    'midnight': ('pitch black', 'oled'),
    'contrast': ('high visibility',),
}


def _choice_needles(setting):
    """Searchable spellings of each choice, as (needle, value) pairs.

    The stored value, the display label, and any aliases are all offered, with
    underscores read as spaces, so ``sets_reps_weight`` is found by "sets reps
    weight" as well as by its label.
    """
    needles = []
    for value, label in setting.choices:
        spellings = [label.split('—')[0], str(value).replace('_', ' ')]
        spellings.extend(CHOICE_ALIASES.get(str(value), ()))
        for raw in spellings:
            needle = _normalise(raw).strip()
            if needle:
                needles.append((needle, value))
    return needles


def _extract_number(query, setting):
    """Pull the number a request is asking for, honouring simple unit words.

    "rest 90 seconds" against a setting stored in seconds is 90; the same
    phrase written as "rest 2 minutes" is 120.
    """
    normalised = _normalise(query)
    # "one day", "two minutes" — spell small word-numbers as digits first.
    words_to_digits = {'zero': '0', 'one': '1', 'two': '2', 'three': '3',
                       'four': '4', 'five': '5', 'six': '6', 'seven': '7',
                       'eight': '8', 'nine': '9', 'ten': '10'}
    normalised = re.sub(
        r'\b(' + '|'.join(words_to_digits) + r')\b',
        lambda m: words_to_digits[m.group(1)], normalised)
    matches = re.findall(r'(\d+(?:\.\d+)?)\s*(%|percent|s|sec|secs|second|seconds|m|min|mins|minute|minutes)?',
                         normalised)
    for raw, unit in matches:
        try:
            number = float(raw)
        except ValueError:
            continue
        unit = (unit or '').strip()
        if setting.unit.strip() == 's' and unit in ('m', 'min', 'mins', 'minute', 'minutes'):
            number *= 60
        elif setting.unit.strip() in ('min', 'min.') and unit in ('s', 'sec', 'secs', 'second', 'seconds'):
            number /= 60
        return number
    return None


def _boolean_intent(query):
    """Whether a request reads as switching something on or off, or neither."""
    normalised = f' {_normalise(query)} '
    negative = max((normalised.find(f' {hint.strip()} ') for hint in _NEGATIVE_HINTS
                    if f' {hint.strip()} ' in normalised), default=-1)
    positive = max((normalised.find(f' {hint.strip()} ') for hint in _POSITIVE_HINTS
                    if f' {hint.strip()} ' in normalised), default=-1)
    if negative < 0 and positive < 0:
        return None
    # A later cue wins: "turn on sound, actually no, turn it off".
    return positive > negative


# Positive words that point at whatever the sentence is *about* rather than at
# a switch: wishes ("I want the bell sound" wants a bell, not an unmute) and
# directions ("and louder volume" turns up a number, it does not unmute).
# Only 'turn on'/'switch on'/'enable'/'on' name a toggle outright; the rest
# still read as positive intent, but a toggle they alone would switch on is an
# assumption, re-examined in :func:`match_intent`.
_SOFT_POSITIVE_HINTS = ('want', 'give me', 'show', 'add', 'let me', 'allow',
                        'always', 'more', 'bigger', 'larger', 'longer',
                        'higher', 'raise', 'increase', 'up', 'louder')

_POSITIVE_HINTS_EXPLICIT = tuple(h for h in _POSITIVE_HINTS
                                 if h not in _SOFT_POSITIVE_HINTS)


def _positive_cue(query):
    """Which positive hint decided the query's intent, or None if none did.

    Among hints of the same kind a later cue wins, as in
    :func:`_boolean_intent`. But a word that names a switch outright beats a
    soft one wherever it sits: "turn on the bell sound and louder volume" is
    still an explicit unmute, even though "louder" comes last and is only
    speaking for the volume.
    """
    normalised = f' {_normalise(query)} '
    best, cue = -1, None
    for hints in (_POSITIVE_HINTS_EXPLICIT, _SOFT_POSITIVE_HINTS):
        for hint in hints:
            at = normalised.find(f' {hint.strip()} ')
            if at > best:
                best, cue = at, hint
        if best >= 0:
            return cue
    return None


def _has_direct_evidence(setting, query):
    """Whether the query names this setting itself — keyword or label, not
    merely one of its choice values. Guards list-typed settings: "streak"
    appearing in a sentence must not rewrite the dashboard tiles just because
    a tile happens to be called that."""
    normalised = _normalise(query)
    padded = f' {normalised} '
    query_stems = _stems(normalised.split())
    for keyword in setting.keywords:
        key_norm = _normalise(keyword).strip()
        if not key_norm:
            continue
        key_words = key_norm.split()
        if f' {key_norm} ' in padded:
            return True
        if all(_stem(word) in query_stems for word in key_words):
            return True
    label_stems = _stems(_tokens(setting.label))
    return bool(label_stems and label_stems <= query_stems)


def _evidence_stems(setting, query):
    """Which words of the query this setting's own vocabulary accounts for.

    The same three sources :func:`score_setting` weighs — keywords, label,
    choice spellings — but reported as the set of query stems they explain
    rather than as a number. Used to spot a setting whose entire claim on a
    sentence is already explained by another, better-fitting one.
    """
    normalised = _normalise(query)
    padded = f' {normalised} '
    query_stems = _stems(normalised.split())
    found = set()
    for keyword in setting.keywords:
        key_norm = _normalise(keyword).strip()
        if not key_norm:
            continue
        key_stems = _stems(key_norm.split())
        if f' {key_norm} ' in padded or key_stems <= query_stems:
            found |= key_stems & query_stems
    found |= _stems(_tokens(setting.label)) & query_stems
    for needle, _value in _choice_needles(setting):
        if len(needle) > 2 and needle not in _GENERIC_CHOICE_WORDS and f' {needle} ' in padded:
            found |= _stems(needle.split()) & query_stems
    return found


STRONG_MATCH = 6.0   # confident on its own, even alongside stronger matches
WEAK_MATCH = 3.0     # only trusted when nothing scored better


def match_intent(query, limit=3, current=None):
    """Resolve a plain-language request into concrete setting changes.

    Pure function over the registry — no model, no network, no I/O. Returns a
    list of ``{'key', 'value'}`` dicts in the same shape the model produces, so
    both paths feed the identical validation in :func:`apply_changes`.

    Two-tier acceptance: a strong score always proposes, so one sentence can
    change several settings ("dark mode with bigger text and no sound"); a
    weak score proposes only when it is the best on offer, so a glancing word
    match cannot ride along beside a confident one.

    ``current`` is the user's present settings, used two ways: to break ties
    ("use pounds not kilograms" names both units — the one not already in
    effect is the one being asked for), and as the base that list-typed
    settings merge into.
    """
    if not str(query or '').strip():
        return []

    current = current or {}
    ranked = sorted(
        ((score_setting(s, query), s) for s in SETTINGS),
        key=lambda pair: pair[0], reverse=True,
    )

    candidates = []
    for rank, (score, setting) in enumerate(ranked[:limit]):
        if score < WEAK_MATCH or (score < STRONG_MATCH and rank > 0):
            break
        value = None
        assumed = False

        if setting.type == 'bool':
            intent = _boolean_intent(query)
            # Naming a toggle with no on/off word is taken as asking for it
            # ("big timer"), and so is a bare wish ("I want the bell sound").
            # Both are assumptions, re-examined below; "turn on"/"no sound"
            # name the toggle outright and are not.
            assumed = (intent is None or
                       (intent is True and _positive_cue(query) in _SOFT_POSITIVE_HINTS))
            if setting.intent_inverted and intent is not None:
                intent = not intent
            value = True if intent is None else intent

        elif setting.type in ('int', 'float'):
            number = _extract_number(query, setting)
            if number is None:
                # "more rest" / "bigger text" nudge by a couple of steps rather
                # than doing nothing. Direction comes from the keyword that
                # matched, not the sentence as a whole — in "bigger text and
                # no sound" the "no" belongs to the sound, not the text.
                intent = _keyword_direction(setting, query)
                if intent is None:
                    intent = _boolean_intent(query)
                if intent is None:
                    continue
                base = current.get(setting.key, setting.default)
                try:
                    base = float(base)
                except (TypeError, ValueError):
                    base = float(setting.default)
                value = base + (setting.step or 1) * (2 if intent else -2)
            else:
                value = number

        elif setting.type == 'enum':
            value = _best_choice(setting, query, current.get(setting.key))
            if value is None:
                continue

        elif setting.type == 'multi':
            # Lists are merged, never replaced: "show the heatmap" adds one
            # card, it does not silently drop every other card. Wholesale
            # replacement is a decision for the settings page, not a sentence.
            if not _has_direct_evidence(setting, query):
                continue
            padded = f' {_normalise(query)} '
            hits, seen = [], set()
            for needle, choice_value in _choice_needles(setting):
                if choice_value in seen or len(needle) < 3:
                    continue
                if f' {needle} ' in padded:
                    seen.add(choice_value)
                    hits.append(choice_value)
            if not hits:
                continue
            base = current.get(setting.key, setting.default)
            base = list(base) if isinstance(base, (list, tuple)) else []
            if _boolean_intent(query) is False:
                value = [v for v in base if v not in hits]
            else:
                value = base + [h for h in hits if h not in base]
            if value == base:
                continue

        else:  # text — too open-ended to infer safely
            continue

        candidates.append((setting, value, assumed))

    # A toggle switched on purely by assumption must earn its place: if every
    # word it matched is already explained by another setting the sentence
    # picked out, it is riding along, not being asked for. "I want the bell
    # sound" chooses the bell cue — the word "sound" is the cue's own noun,
    # not a request to unmute. An explicit "no sound"/"turn on sound" carries
    # its own intent and is never filtered.
    explained = [(s, _evidence_stems(s, query))
                 for s, _v, assumed in candidates if not assumed]
    proposals = []
    for setting, value, assumed in candidates:
        if assumed:
            mine = _evidence_stems(setting, query)
            if mine and any(other is not setting and mine <= theirs
                            for other, theirs in explained):
                continue
        proposals.append({'key': setting.key, 'value': value})

    return proposals


_UP_WORDS = {'bigger', 'larger', 'longer', 'higher', 'more', 'louder',
             'increase', 'raise', 'faster'}
_DOWN_WORDS = {'smaller', 'shorter', 'lower', 'less', 'quieter', 'reduce',
               'decrease', 'slower'}


def _keyword_direction(setting, query):
    """Direction implied by whichever of the setting's keywords matched.

    Returns True (up), False (down), or None when no matched keyword carries
    a direction word. Scoped to the keyword so one sentence can push two
    numbers opposite ways.
    """
    normalised = _normalise(query)
    padded = f' {normalised} '
    query_stems = _stems(normalised.split())
    for keyword in setting.keywords:
        key_norm = _normalise(keyword).strip()
        key_words = key_norm.split()
        if not key_norm:
            continue
        matched = (f' {key_norm} ' in padded or
                   all(_stem(word) in query_stems for word in key_words))
        if not matched:
            continue
        for word in key_words:
            if word in _UP_WORDS:
                return True
            if word in _DOWN_WORDS:
                return False
    return None


def _best_choice(setting, query, current_value=None):
    """The choice a query is asking for, or None.

    Two disambiguation rules, in order:

    * a match wholly contained in a longer match is noise — "reps" inside
      "sets, reps and weight" is not a vote for the reps-only mode;
    * among what remains, a value that differs from the current one wins —
      "use pounds not kilograms" names both units, but the one being asked
      for is the one not already in effect.
    """
    padded = f' {_normalise(query)} '
    hits = []
    for needle, value in _choice_needles(setting):
        if len(needle) > 2 and f' {needle} ' in padded:
            hits.append((needle, value))
    if not hits:
        return None

    survivors = [
        (needle, value) for needle, value in hits
        if not any(needle != other and needle in other for other, _ in hits)
    ]
    survivors.sort(key=lambda pair: (pair[1] != current_value, len(pair[0])), reverse=True)
    return survivors[0][1]


# ── Presets ──────────────────────────────────────────────────────────
#
# Whole-app configurations reachable in one tap. These exist so a new user
# never has to visit eighty switches to get a setup that suits them.

PRESETS = {
    'minimal': {
        'label': 'Keep it simple',
        'help': 'Timer only, no logging, nothing extra on screen. The fastest way to just train.',
        'icon': 'circle',
        'values': {
            'ui.mode': 'simple',
            'session.tracking_mode': 'timer',
            'session.show_sidebar': False,
            'session.rpe_prompt': False,
            'dashboard.cards': ['today', 'quick_actions'],
            'dashboard.kpis': ['streak', 'sessions'],
            'progress.charts': ['weekly'],
            'timers.auto_advance': True,
            'session.autosave': True,
            'workout.include_warmup': False,
            'workout.include_cooldown': False,
        },
    },
    'balanced': {
        'label': 'Balanced',
        'help': 'The defaults — enough structure to see progress without a control panel.',
        'icon': 'scale',
        'values': {
            'ui.mode': 'simple',
            'session.tracking_mode': 'timer',
            'session.show_sidebar': True,
            'dashboard.cards': ['today', 'quick_actions', 'goals', 'recent'],
            'dashboard.kpis': ['exercises', 'avg_time', 'streak', 'sessions'],
            'progress.charts': ['domain', 'weekly'],
            'workout.include_warmup': True,
            'workout.include_cooldown': False,
        },
    },
    'strength': {
        'label': 'Lifting',
        'help': 'Sets, reps and weight logging with longer rests and heavier defaults.',
        'icon': 'dumbbell',
        'values': {
            'ui.mode': 'advanced',
            'session.tracking_mode': 'sets_reps_weight',
            'session.default_sets': 4,
            'session.default_reps': 8,
            'timers.rest_source': 'fixed',
            'timers.fixed_rest_seconds': 90,
            'timers.auto_advance': False,
            'workout.default_domains': ['Strength'],
            'workout.default_difficulty': 'intermediate',
            'workout.ordering': 'hardest_first',
            'workout.include_warmup': True,
            'workout.include_cooldown': True,
            'session.rpe_prompt': True,
            'progress.charts': ['domain', 'weekly', 'trend'],
        },
    },
    'quiet': {
        'label': 'Quiet & calm',
        'help': 'No sound, no vibration, no animation. Trains fine in a shared room.',
        'icon': 'moon',
        'values': {
            'audio.enabled': False,
            'audio.countdown_ticks': False,
            'audio.voice_announce': False,
            'audio.vibrate': False,
            'appearance.reduce_motion': True,
            'appearance.theme': 'dark',
            'timers.warning_seconds': 0,
        },
    },
    'accessible': {
        'label': 'Easy to see and hear',
        'help': 'Large text, high contrast, oversized timer and spoken exercise names.',
        'icon': 'eye',
        'values': {
            'appearance.theme': 'contrast',
            'appearance.font_size': 19,
            'appearance.density': 'comfortable',
            'appearance.reduce_motion': True,
            'timers.big_timer': True,
            'timers.prep_seconds': 10,
            'audio.enabled': True,
            'audio.volume': 85,
            'audio.voice_announce': True,
            'ui.show_tips': True,
        },
    },
    'everything': {
        'label': 'Show me everything',
        'help': 'Every option unhidden, every panel on, full logging and diagnostics.',
        'icon': 'settings-2',
        'values': {
            'ui.mode': 'expert',
            'session.tracking_mode': 'sets_reps_weight',
            'session.show_sidebar': True,
            'session.rpe_prompt': True,
            'dashboard.cards': ['today', 'quick_actions', 'goals', 'saved', 'recent', 'heatmap'],
            'dashboard.kpis': ['exercises', 'avg_time', 'streak', 'sessions', 'minutes', 'goal'],
            'progress.charts': ['domain', 'weekly', 'heatmap', 'trend'],
            'advanced.developer_mode': True,
            'ui.command_palette': True,
        },
    },
}


def client_payload(values):
    """Everything the browser needs to render and reason about settings.

    The schema travels with the values so the settings page, the command
    palette and the assistant's review card all read one description of each
    control rather than duplicating it in JavaScript.
    """
    return {
        'values': values,
        'schema': [
            {
                'key': s.key, 'label': s.label, 'group': s.group, 'type': s.type,
                'tier': s.tier, 'help': s.help, 'unit': s.unit,
                'choices': [{'value': v, 'label': l} for v, l in s.choices],
                'min': s.minimum, 'max': s.maximum, 'step': s.step,
                'default': list(s.default) if isinstance(s.default, list) else s.default,
            }
            for s in SETTINGS
        ],
        'groups': [{'name': g, 'icon': GROUP_ICONS.get(g, 'settings')} for g in GROUP_ORDER],
        'presets': [{'key': k, **{kk: vv for kk, vv in p.items() if kk != 'values'}}
                    for k, p in PRESETS.items()],
    }
