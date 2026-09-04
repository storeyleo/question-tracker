# Question Tracker

## Purpose

Question Tracker is a local desktop application for organising university
problem sheets, solutions and past papers and tracking performance on
individual questions.

The initial target platform is Windows, but the architecture should avoid
unnecessary Windows-specific dependencies so that macOS and Linux versions
remain practical in the future.

---

## Technology

- Language: Python
- Desktop framework: PySide6 / Qt 6
- Database: SQLite
- Initial platform: Windows
- Future platforms: macOS and Linux
- Application is local-only with no accounts or authentication.

Platform-independent APIs and pathlib should be preferred over
Windows-specific implementations where practical.

---

## Managed File Library

Question Tracker maintains its own file library.

Example:

Question Tracker/
└── files/
    └── Year 2/
        └── Linear Algebra/
            ├── Problem Sheets/
            ├── Problem Sheet Solutions/
            └── Past Papers/

Year and module names are user-created folder names rather than separate
metadata-heavy entities.

Users may create folders and copy PDFs into the library manually.

The application should detect newly created folders and PDFs.

Newly detected PDFs may remain in a "Needs setup" state until configured.

### File types

The three primary imported document types are:

- Problem Sheet
- Solution
- Past Paper

A problem sheet or past paper normally has one main PDF.

A question set may have zero, one or multiple associated solution/reference
PDFs.

Solution association is manual initially. Filename-based suggestions may be
added later.

If a managed PDF is deleted outside the application, its tracking data should
remain and the document should be displayed as missing so that the PDF can be
replaced.

Replacing a PDF must preserve questions, attempts, notes, bookmarks and other
tracking data.

Paths stored in the database should be relative to the managed library root
where practical.

---

## Question Sets

Problem sheets and past papers use the same underlying question-set model.

Each question set may contain:

- Main PDF
- Associated solution/reference PDFs
- Display name independent of filename
- Questions
- Topic headings
- General notes
- Completion/mastery information

---

## Question Structure

Questions support up to three levels.

Examples:

1
1.a
1.a)i

Only leaf questions are markable.

A question that has children acts only as a parent/container.

Question identity must be independent of its visible number/label so that
reordering does not lose tracking history.

Question navigation follows visible left-to-right order.

Example:

1.a → 1.b → 1.c → 2.a → 2.b → 3 → 4.a)i

### Creating question structures

A PDF may be imported without immediately creating its question structure.

The initial implementation may use a simple manual question editor.

The intended future interface is similar to Microsoft Word's table-size
picker:

- Drag horizontally to create main questions.
- Drag downward from a question to create subquestions.
- Drag downward again to create third-level subquestions.

Question structures can be edited later from the Question Tracker screen.

Questions should support drag-and-drop reordering.

If a marked parent question is later split into children, its existing history
moves to the new leftmost leaf by default.

Deleting a question containing tracking data must warn the user and provide
options to:

- Cancel
- Permanently delete the data
- Move the history to another question

---

## Topic Headings

Questions may belong to multiple topic headings.

Examples:

- Bases
- Linear Independence
- Eigenvalues

Headings are manually created and assigned.

They contain a title only.

In the tracker UI, headings should visually span the questions assigned to
them.

Topic headings should be reorderable.

---

## Attempts

Every time a leaf question is assigned a colour/status, a new attempt is
created automatically.

There is no separate "new attempt" action.

Each attempt stores:

- Status
- Timestamp
- Optional note

Existing attempts can be selected and edited if the user marked one
incorrectly.

Attempt history must not be overwritten.

### Statuses

Initial default statuses/keybinds:

1 = Green
    Correct

2 = Orange
    Partial / needs reattempt

3 = Red
    Incorrect

4 = Dark red
    Did not know how to start

White means that no attempt exists and is not itself an attempt status.

Colours and keybinds may become user-configurable later.

### Attempt display

Attempts are displayed as offset/overlapping coloured boxes.

The oldest attempt is furthest behind.

The newest attempt is on top.

Previous attempts remain partially visible behind the latest attempt so that
the question's history can be understood at a glance.

---

## Keyboard Navigation

The marking workflow should be extremely fast.

Default controls:

Left / Right Arrow
    Move between leaf questions.

1 / 2 / 3 / 4
    Add an attempt with the corresponding status.

Keyboard shortcuts should be implemented so that an entire problem sheet can
be marked with minimal mouse interaction.

Custom keybinds may be implemented later.

---

## Notes and Bookmarks

Three levels of notes are supported:

- Question-set note
- Question note
- Attempt note

Question and attempt notes should normally appear only when clicked or hovered
rather than permanently occupying tracker space.

Questions can also be bookmarked.

A bookmark means:

"This question was useful and I should return to it when revising."

Bookmarks are independent of attempt colour/status.

---

## Completion and Mastery

A question set is considered Attempted/Complete when every leaf question has
at least one attempt.

A question set is Mastered when every leaf question's latest attempt is green.

Orange, red or dark-red questions therefore do not make a question set
unattempted, but they prevent it from being mastered.

Unconfigured PDFs are shown separately as "Needs setup" rather than incomplete.

Colour filtering should use each question's latest attempt.

Future functionality should include sorting/filtering by colour to quickly
find questions requiring reattempts.

---

## Main UI

### Home Screen

The home screen contains module cards.

Each module shows counts for problem sheets and past papers, including
completion information.

Clicking a module opens the Question Tracker screen.

Below the module cards is a navigable quick-access file browser.

Below that is an upload/import section for adding:

- Problem Sheets
- Solutions
- Past Papers

Users should also be able to manipulate folders/files directly inside the
managed file library.

---

## Question Tracker Screen

The Question Tracker screen contains:

Left:
    Collapsible file/module tree.

Right:
    Core question tracker with topic headings and coloured attempt boxes.

The tree should support:

- Expand/collapse
- Expand all
- Navigation between modules and question sets

Solution PDFs are hidden from this screen because they are primarily used
during marking.

The tracker provides an Edit function for modifying question structure and
topics.

Colour/status controls remain visible while using the tracker.

---

## Marking Workspace

The marking workspace is primarily for checking the user's work against
solutions and rapidly recording results.

It contains two independent PDF/file panes.

Each pane can initially display a file directory.

Selecting a document opens the PDF in that pane.

A "<" / back control returns the pane to its file browser so another document
can be selected.

Solutions are visible within this workspace.

The interface should support commonly expected PDF controls including:

- Page navigation
- Zoom
- Fit to width
- Fit to page
- Rotate
- Search
- Jump to page

A compact/collapsible question-tracker strip should also be available in the
marking workspace so the user can record attempts while viewing both PDFs.

---

## Future UI Features

Not required for the first build but the architecture should not prevent:

- Progress statistics
- Colour filtering/sorting
- Reattempt workflow
- Configurable colours
- Configurable keybinds
- Improved graphical question-structure builder
- Filename-based solution suggestions

---

## Persistence

All changes should be saved automatically to SQLite as they occur.

The application should support Undo and Redo.

Default shortcuts:

Ctrl+Z = Undo
Ctrl+Y = Redo

Visible Undo/Redo controls should also be available.

The implementation should distinguish between correcting/editing an existing
attempt and undoing the creation of an attempt.

---

## Backups

The application should automatically create periodic database backups.

A limited number of recent backups should be retained so storage usage does
not grow indefinitely.

Exact backup frequency and retention count can be decided during
implementation.

---

## Export and Import

Two export types are desirable:

### Database-only export

Exports tracking data without PDFs.

### Portable export

Creates a portable archive approximately:

QuestionTrackerExport.zip
├── question-tracker.db
└── files/
    ├── Year 1/
    ├── Year 2/
    └── ...

The portable bundle should be usable when moving between computers and,
eventually, operating systems.

Initial portable import behaviour may replace the current library.

Merging two libraries can be implemented later.

Application preferences such as custom colours, keybinds and window layout do
not need to be included initially.

---

## Development Principles

The first build should prioritise:

- Correct data modelling
- Reliable persistence
- Basic file management
- Basic question tracking
- Fast keyboard marking
- Maintainable architecture

Advanced visual polish and optional features should be added after the core
workflow is reliable.