# skill

Load a skill's instructions by name.

## Parameters

- `name` (string, required) — Name of the skill to load.

## Examples

skill(name="commit")
skill(name="review")

## Notes

- Each skill is loaded once per session; subsequent calls return an "already loaded" message.
- Returns the instruction text for the skill, which guides further actions.
