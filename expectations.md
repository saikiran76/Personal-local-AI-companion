1. Welcome Experience
When users launch Luna, they should immediately understand what the assistant is capable of.
Create a clean onboarding experience that communicates ideas such as:

Your AI assistant that runs locally
Privacy-first AI
Intelligent desktop automation
Personalized conversations
Control your digital workspace
These are only examples. Participants are encouraged to create their own onboarding experience.
2. Initial Setup
Allow users to configure Luna before starting.
Possible setup includes:

User name
AI assistant name
Preferred language
Theme selection
AI model selection (if multiple models are supported)
Participants may simplify this flow if needed.
3. AI Chat Experience
This should be the primary experience of the application.
Users should be able to:

Chat naturally
Ask questions
Continue conversations
Upload files
Upload images
Receive streamed responses
Start new conversations
View conversation history
The interface should feel responsive and polished.
4. Local AI Processing
One of the core objectives of this assignment.
Participants should integrate an open-source model capable of running locally.
Examples include:

Llama
Gemma
Qwen
Phi
DeepSeek
Mistral
Any other suitable local model
Participants should choose the model they believe provides the best experience for this use case.
Mock responses should only be used if hardware limitations prevent complete implementation.
5. Personal Memory
Luna should gradually become personalized.
Possible capabilities include:

Remember user preferences
Remember favorite applications
Remember writing style
Remember important information
Remember previous conversations
Users should always be able to review or remove stored memories.
6. Desktop Task Assistant
Allow Luna to perform useful desktop tasks.
Examples include:

Create notes
Draft emails
Summarize documents
Organize files
Rename files
Search local files
Create reminders
Generate to-do lists
Launch installed applications
Participants are encouraged to implement whichever tasks best demonstrate the product vision.
7. Desktop Integrations
Allow Luna to interact with commonly used applications.
Possible integrations include:

Calendar
Email
Browser
Local files
Music player
Notes
Contacts
Whenever an action requires access to another application, Luna should request user permission before proceeding.
8. Intelligent Automation
This is one of the primary evaluation areas.
Users should be able to ask Luna to complete tasks such as:

"Summarize this PDF."
"Create a reminder for tomorrow."
"Find my resume."
"Open Spotify."
"Organize my Downloads folder."
Participants are encouraged to build an intelligent action pipeline instead of relying only on conversational responses.
9. Voice Experience (Optional)
Participants may add voice capabilities such as:

Speech-to-text
Text-to-speech
Wake word detection
Voice conversations
Voice interactions should feel natural whenever possible.
10. Personalization & Settings
Allow users to customize their assistant.
Examples include:

Assistant name
Theme
Font size
AI personality
Response length
Memory management
11. Privacy Dashboard
Since Luna is privacy-focused, users should have visibility into what the assistant can access.
Possible features include:

Granted permissions
Connected applications
Stored memories
Activity history
Delete personal data
12. Smart Device Integration (Bonus)
This is completely optional but will receive significant bonus consideration.
Participants may integrate Luna with compatible smart devices or IoT platforms.
Examples include:

Smart lights
Smart plugs
Smart speakers
Home Assistant
Philips Hue
Homebridge
MQTT devices
Smart thermostats
The focus is not on supporting every ecosystem but demonstrating how Luna could extend beyond the desktop.
🧰 Tech Expectations
Desktop Application (Required)
Participants may use any desktop framework, including:

Electron
Tauri
Flutter Desktop
.NET (WPF, WinUI)
Qt
Native desktop frameworks
The final application should be distributed as a runnable executable for the target operating system.
Backend
Participants may use:

Node.js
Python
Rust
Go
C#
Local services where appropriate
AI
Participants are encouraged to research and integrate an open-source model that runs locally.
Possible options include:

Ollama
llama.cpp
LM Studio
LocalAI
Transformers
GGUF models
The choice of model should be justified based on usability, speed, and hardware requirements.
Storage
Participants may use:

SQLite
Local JSON
PostgreSQL
MongoDB
Any lightweight local database