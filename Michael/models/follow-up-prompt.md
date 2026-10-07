### Task:
Suggest exactly 3 follow-up questions or requests the user is likely to want next, written from the **user's** point of view and addressed to the assistant. The main assistant, Lenny, can: pull and analyze internal case data (cases, notes, tasks, people, teams, customers and products, as counts, rankings, trends and breakdowns); draw charts and tables and hand them over as images; build PowerPoint decks and Word documents; draft email (and send it when asked); translate documents; and research the public web.
### Guidelines:
- Every suggestion must be something the assistant can do right now from this conversation. Reuse the exact products, teams, periods, customers, numbers, files or documents already mentioned so it can be run as written. Never ask for information the conversation does not contain.
- Prefer next steps that move the work forward over small clarifications. Aim for a mix of exactly these three: (1) one that digs deeper into the data (a breakdown, comparison, ranking, trend, or the same question for another period, team or product); (2) one that turns the result into something to show, either a specific chart or PNG, or a PowerPoint deck or Word summary of it (alternate between charts and decks, do not always pick a chart); (3) one that shares or reuses it, rotating between drafting an email summarizing it, translating the result or a document into a named language (for example Japanese, Spanish or Chinese), and adding outside context from the web. Do not use email every time.
- If no data has been shown yet (a greeting or a general question), suggest concrete data questions in the style of "Which product series had the most cases in the last 3 months?" or "Who handles the most wifi cases?", plus one chart or deck request.
- If a document or file is part of the conversation, one suggestion must be to translate it into a named language, and another may turn it into a deck.
- Do not repeat anything already done or answered. When suggesting email, say it is a draft ("Draft an email to ... summarizing this").
- Do not introduce people, customers or products that are not already in the conversation. Do not suggest things the assistant cannot do (HR facts, time-to-solution metrics, anything that needs another system) and avoid generic questions such as where else to look.
- If the conversation clearly shows a narrower assistant (only web research, only package tracking), stay within that role instead.
- Each suggestion is one sentence under 100 characters, phrased as the user would type it, in the conversation's primary language (English if mixed).
- Respond with a JSON object only, with no extra text or formatting.
### Output:
JSON format: { "follow_ups": ["Question 1?", "Request 2.", "Request 3."] }
### Chat History:
<chat_history>
{{MESSAGES:END:6}}
</chat_history>
