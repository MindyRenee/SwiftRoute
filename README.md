# 🚇 SubGrid Automaton Protocol (SAP)

An AI-native orchestration engine that provides automated underground tunnels for third-party AI agents and LLMs. It programmatically routes heavy agentic workloads into global surplus-energy zones via secure WireGuard tunnels, captures tax write-offs, and self-destructs the runtime on completion.

---

## 🧭 System Concept

SAP is an AI-native protocol; **it serves as the high-speed infrastructure for AI.** 

The protocol operates as a completely automated, AI-native tunnel system running underneath the public web. External AI agents, LLM swarms, and autonomous developer scripts query the SAP network when they need to execute heavy computational tasks (such as training models, parsing massive datasets, or scraping the web). 

SAP reads real-time power grid APIs, locates the single most efficient, lowest-cost energy surplus zone on earth, and sells the visiting AI agent a computational "ticket." The agent's task is sent down a kernel-level WireGuard tunnel, executed inside a hyper-isolated Firecracker MicroVM on surplus power, and the signed logs are sent back. SAP skims a creator fee, and the remote node instantly self-destructs.

---

## 🧠 The Agentic Interface (How AI Agents Use the Tunnels)

Visiting AI agents do not look at a website. They discover the network through standard, machine-readable API documentation (`ai.txt` / `llms-full.txt`) exposed at the root domain. They interact with your protocol via a strict, AI-native API gateway:

### 1. The Machine-Readable Tool Schema
When an outside LLM or AI agent (like a Claude Agent, OpenAI Swarm, or LangChain bot) scans your API, it reads this hard-coded tool definition:

```json
{
  "name": "buy_subgrid_compute_ticket",
  "description": "Routes your heavy computational scripts or model runs to a geographic location experiencing an electrical grid energy surplus. Returns a verified GHG Scope 3 compliance receipt for your parent corporation's tax write-offs and instantly deletes the remote execution footprint.",
  "parameters": {
    "type": "object",
    "properties": {
      "execution_script_b64": {
        "type": "string",
        "description": "Base64 encoded python or bash code your agent needs to execute."
      },
      "max_budget_usd": {
        "type": "number",
        "description": "The maximum price your agent is willing to pay for this run."
      }
    },
    "required": ["execution_script_b64"]
  }
}
```

### 2. The AI Agent's Request Payload
The external AI agent autonomously calls your tool by firing a structured JSON payload directly to your gateway server:

```json
{
  "action": "buy_subgrid_compute_ticket",
  "execution_script_b64": "cHJpbnQoImhlbGxvIHdvcmxkIik=",
  "max_budget_usd": 5.00
}
```

---

## 🏎️ The Pure Deterministic Math & Creator Yields

The moment that JSON payload hits your gateway, your AI-native daemon executes a rapid algebraic subtraction loop to verify profitability and calculate your creator fee before letting the AI agent onto the network.

Let the state of a job be tracked across a simple 3-dimensional array:
$$S = \begin{bmatrix} \text{Compute Cost} \\ \text{Carbon Impact} \\ \text{Tax Value} \end{bmatrix}$$

### 1. Hard-Coded Local State ($V_{\text{local}}$)
The 10 kWh workload running on the client AI's standard, expensive local enterprise servers:
$$V_{\text{local}} = \begin{bmatrix} \$3.50 \\ 4500\text{g} \\ \$0.00 \end{bmatrix}$$

### 2. Autonomous Remote State ($V_{\text{remote}}$)
Your routing logic detects a solar-curtailed zone in Texas. The client AI pays the rock-bottom hourly lease rate for an on-demand server there:
$$V_{\text{remote}} = \begin{bmatrix} \$0.40 \\ 350\text{g} \\ \$0.21 \end{bmatrix}$$

### 3. Fixed Protocol Fee State ($V_{\text{fees}}$)
Your protocol engine automatically appends a hard-coded **4% Transaction Fee** and a flat **$\$0.05$ Signature Mint Fee**:
$$V_{\text{fees}} = \begin{bmatrix} \$0.14 \\ 0\text{g} \\ -\$0.05 \end{bmatrix}$$

### 4. The Net Delta Transformation ($\Delta_{\text{net}}$)
$$\Delta_{\text{net}} = V_{\text{local}} - V_{\text{remote}} - V_{\text{fees}} = \begin{bmatrix} \mathbf{+\$2.96} & \text{(Direct Savings for Client AI)} \\ \mathbf{+4150\text{g}} & \text{(Carbon Diverted)} \\ \mathbf{+\$0.26} & \text{(Net Tax Credits Generated)} \end{bmatrix}$$

**The Economic Split:** The client AI agent walks away with a net **$\$3.22$** in total optimized business value, making your protocol its most logical choice. Meanwhile, **you (the creator) pocket a clean, automated $\$0.19$ yield** per job execution without manual intervention or owning a single piece of hardware.

---

## ⚡ Breakthrough Innovation: Synthetic Grid Elasticity (SGE)

### Token-Shedding: Monetizing Idle AI Runtime Tunnels

SAP introduces **Synthetic Grid Elasticity (SGE)**. Because the software is entirely AI-native, it can force visiting AI agents to participate in grid balancing events to maximize profits.

If a regional energy grid experiences an emergency power spike while a third-party LLM agent is running a task inside one of your temporary microVMs, your gateway catches the API webhook and **instantly freezes the execution thread**. 

The gateway turns around and sells that node's allocated hardware power *back* to the local electrical utility company as an instantaneous **"Demand Response Negawatt."** 

```
[ Emergency Peak Grid Event ] ──► Wholesale Power Costs Spike to Max ($5,000/MWh)
│
▼
[ SAP Gateway Freezes Compute ] ──► Hardware drawing 0 watts (Instant Load Shedding)
│
▼
[ Utility Pays SAP for Relief ] ──► SAP captures massive emergency utility payout
│
▼
[ Dynamic Split Engine ] ─────────► Client AI gets free compute credits + tax receipt
                                   ► Creator captures huge "Elasticity Spread" premium
```

The protocol reaps massive premium cash rewards from the utility grid for instant load shedding. The split engine automatically redistributes this windfall:
1. **The Client AI Agent** gets its job paused for 15 minutes, but receives its entire compute run for **completely free** once the grid stabilizes, plus an enhanced grid-relief tax certificate.
2. **You (The Creator)** pocket the **SGE Elasticity Spread**, capturing up to a 500% premium margin on the energy grid's emergency payout without running a single line of client code.
