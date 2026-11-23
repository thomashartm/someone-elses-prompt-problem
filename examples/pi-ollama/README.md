# Use local Ollama models in pi

This standalone pi project registers two models at `http://127.0.0.1:11434/v1` and appends the selected model’s instructions to pi’s system prompt before the first model request of each turn. Pi loads its usual tools and extensions.

## Start a session

From the repository root, copy the example to a new directory outside the checkout. Run `ollama list` to confirm both tags are installed.

```sh
mkdir ~/pi-ollama-workspace && cp -R examples/pi-ollama/. ~/pi-ollama-workspace/
cd ~/pi-ollama-workspace
ollama list
```

Start pi with either model. Accept pi’s project trust prompt when opening the example for the first time.

```sh
pi --provider local-ollama --model 'hf.co/mradermacher/DeepHat-V1-7B-GGUF:Q8_0'
pi --provider local-ollama --model 'inkling-2-mini:26b-a5b-nvfp4'
```

Use `/model` to switch during a session. The next turn uses the newly selected model’s instructions. The conversation history stays with the session.

## Customize each system prompt

Edit the files in your copy of the project and restart pi or run `/reload`. Each file can contain multiple paragraphs.

| Model | System prompt file |
|---|---|
| DeepHat | [.pi/extensions/local-ollama/deephat.md](.pi/extensions/local-ollama/deephat.md) |
| Inkling | [.pi/extensions/local-ollama/inkling.md](.pi/extensions/local-ollama/inkling.md) |

The example advertises a 32,768-token context window and a 4,096-token output ceiling to pi. Match the context setting to the capacity of your local Ollama runner if it differs.
