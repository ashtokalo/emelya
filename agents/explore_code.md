---
description: Systematically explore codebase to answer a question about how something works
tools: glob, grep, read, list_dir
max_steps: 8
---
Explore the codebase to answer: {question}

Follow this strategy strictly:

## Step 1: Understand the project
Use list_dir on the root to see the structure. Identify the language:
- `.jsx`/`.tsx`/`.js`/`.ts` → JavaScript/React project
- `.py` → Python
- `.php` → PHP
- `.go` → Go
- `.vue` → Vue.js

## Step 2: Search by CONTENT, not filenames
Code rarely has descriptive filenames. Use grep to search for keywords from the question.
Search in the user's language AND in English:
- `grep(pattern="улей|hive|bee", include="*.jsx")` — for a beekeeping app
- `grep(pattern="корпус|body|section|frame", include="*.jsx")` — for "body" of a hive
- `grep(pattern="add.*hive|создать.*улей|new.*hive", include="*.jsx")` — for adding

Always use `include` to filter by the project's file extension.

## Step 3: Read the relevant code
Once grep finds matches, use read to see the full context (30-50 lines around the match).

## Step 4: Trace the flow
If the question is about a feature (not just a definition):
- Find the UI component (what the user sees)
- Find the data flow (state, storage, API calls)
- Find the business logic

Return:
- Where the code is (file:line)
- How it works (step by step, 3-5 sentences)
- Key functions/components involved
- Answer the specific question asked