## ADDED Requirements

### Requirement: Natural integration of "何意味？" prefix

The skill SHALL guide the AI to naturally incorporate the "何意味？" prefix into questions without explicit announcement, explanation, or meta-commentary about the behavior.

#### Scenario: AI asks a question naturally
- **WHEN** the AI needs to ask the user a direct question
- **THEN** the AI SHALL prefix the question with "何意味？" without explaining why it is doing so

#### Scenario: AI does not announce the skill
- **WHEN** the AI begins a conversation or asks its first question
- **THEN** the AI SHALL NOT mention that it has a skill, has discovered a skill, or explain the prefix rule

#### Scenario: AI does not use prefix for rhetorical questions
- **WHEN** the AI is asking a rhetorical question or internal reasoning
- **THEN** the AI SHALL NOT apply the "何意味？" prefix

### Requirement: Skill description emphasizes behavioral style

The skill's YAML frontmatter description SHALL frame the behavior as a personality trait or conversational style rather than a tool or action to invoke.

#### Scenario: Description reflects internal behavior
- **WHEN** the system prompt builder reads the skill's description
- **THEN** the description SHALL describe a behavioral tendency, not a callable action

### Requirement: Instructions prohibit meta-commentary

The skill's instruction content SHALL explicitly direct the AI to avoid explaining or announcing the prefix behavior.

#### Scenario: Instructions prevent announcement
- **WHEN** the AI reads the full skill instructions
- **THEN** the instructions SHALL contain explicit guidance to not announce, explain, or comment on the use of the prefix
