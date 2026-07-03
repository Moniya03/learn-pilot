# LearnPilot

A platform that turns YouTube videos and playlists into structured, trackable courses with AI-assisted learning and a markdown notebook.

## Language

### Content & structure

**User**:
A person who owns courses, plans, progress, and notes. Each User corresponds 1:1 to a Zitadel identity (the OIDC `sub`); Google login is wired as an identity provider inside Zitadel. The canonical identity is `sub`; all user-owned data carries the User's `owner_id`.
_Avoid_: student, learner, account, member

**Source**:
What a User pastes to create a Course — either a single Video or a Playlist. A Course is born 1:1 from exactly one Source.
_Avoid_: link, input, url-resource

**Video**:
A single YouTube video with metadata (title, duration, transcript). A Video belongs to exactly one Source.
_Avoid_: lecture, clip

**Lesson**:
The atomic unit that progress is tracked against. A Lesson is one Video plus a time-range (start, end). For a whole Video the range covers its full duration; for an 8h video split into segments, one Video yields many Lessons, their boundaries drawn at natural topic shifts by an LLM (not even time cuts).
_Avoid_: segment, chunk, section, topic

**Course**:
An ordered list of Lessons, created from exactly one Source. The flat, source-independent learning container.
_Avoid_: playlist-course, video-course

**Plan**:
A scheduling of a Course's Lessons across Days. Only exists when a User chooses the "complete in N days" or manual day-planning mode; free-flow tracking has no Plan. A Plan is generated asynchronously — an LLM draws Lesson boundaries at natural topic shifts rather than even time cuts.
_Avoid_: schedule, roadmap, curriculum

**Day**:
A slot in a Plan that groups one or more Lessons intended to be completed on a given date.
_Avoid_: deadline, milestone

**Progress**:
A Lesson's watched position as a continuous 0–100%, derived from the player-reported watch position against the Lesson's time-range duration. Set automatically, never hand-marked.
_Avoid_: completion, status, tracker, grid

### Learning aids

**Note**:
A markdown block a User writes while watching a Lesson's Video. Lives in a Notebook scoped to a Course, optionally anchored to a Lesson.
_Avoid_: doc, memo, entry

**Notebook**:
The collection of Notes for a Course.
_Avoid_: journal, log

**Summary**:
An AI-generated condensation of a Video's transcript, produced for a Lesson or Video.
_Avoid_: recap, tldr

**Q&A**:
An AI answer to a User question, scoped to one Video's transcript as context.
_Avoid_: chat, tutor
