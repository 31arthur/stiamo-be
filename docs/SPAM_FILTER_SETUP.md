# Setting up the Spam filter agent

The Spam filter reads a mailbox, keeps the mail that matters to the workspace, and moves the rest to Trash. You describe the
inbox in plain words; the agent turns that into the instructions the decision model (Jev) reads. Mail is only ever moved to
Trash, never deleted, so anything moved by mistake can be put back from the mailbox.

## Before you start

1. **A mailbox** connected in **Mail** (Mailboxes, Connect a mailbox), in the workspace it belongs to.
2. **An OpenRouter key** in **Agents, Settings, Providers**: Jev is reached through OpenRouter.

## The steps

1. **Agents, New agent, Spam filter.** (Or the `+` beside **Agents** in the sidebar.)
2. **Name it** after the inbox it guards, for example "S&B inbox filter". Under **Workspaces** at the bottom, tick the
   workspace it works in. It then appears under **TTF** in that workspace's sidebar.
3. **What it reads.** Choose the mailbox (or several), then **which emails**:
   - **The whole inbox**: every email, newest first, a few at a time.
   - **The last few days**: for example the last 30 days.
   - **Between two dates**: a From date and an Until date, both included.

   **Emails per run** is how many it decides in one run (25 to 500).
4. **Describe your inbox.** Picking a mailbox fills these boxes with a starting point for that workspace. Change anything:

   | Box | What to write | Example (S&B) |
   |---|---|---|
   | **Who the inbox belongs to** | One line on the organisation or person. | a civil society organisation that works for migrant workers' rights |
   | **Keep** | The mail worth keeping. Be concrete. | genuine correspondence about migrant workers, labour rights, partners, funders, campaigns, events we take part in, or personal one-to-one mail |
   | **Also keep** | Senders or kinds of mail that are always fine. | anything from trade unions, global union federations (for example BWI), labour and human rights bodies, international NGOs |
   | **Spam** | What to clear out. | marketing, promotions, cold sales, newsletters, digests, automated notifications, social media alerts, unrelated solicitations |
   | **When unsure** | What to do on a close call. | When unsure, choose keep. |

   The page shows the exact instructions these boxes make. **Advanced** has the full prompt and the two labels for fine
   control (editing those by hand stops the boxes from rewriting them).
5. **Keep safe.** Rules that are checked in code before the model sees an email, so these are never judged:
   - **Safe senders**: anyone you have written to from the mailbox (read from its Sent folder). Switch it off or on, and
     press **Who is protected?** to see the list.
   - **Domains** to always keep, for example `bwint.org` (subdomains count).
   - **Words or phrases** that always keep an email, matched as whole words in the sender, subject or start of the text.
   - **Addresses** to always keep.
6. **What happens to spam.** Choose **Move to Trash when it is labelled spam**. Leave **Move it by itself when it is
   sure** off at first: confident spam then waits in **Review** for your OK. Turn it on once you trust the results.
7. **Who decides.** Jev is the default. Pick the Jev model (or your default) and **Emails per request** (20 is a good
   start: the instructions are sent once for the whole batch, which costs less).
8. **Show the prompt** to read exactly what Jev is sent.
9. **Test run.** Runs on up to 20 emails and changes nothing. Check the labels and the reasons. If it wrongly calls something
   spam, add it to **Keep** or **Also keep**, or protect its domain, and test again.
10. **Save** (Create agent). On its page, **Run** has **Run now**, and **Run through the whole inbox** which repeats runs until
    everything in range is judged. It never runs by itself: you start it.

## How it decides

For each email Jev answers a multiple-choice question (keep or spam) with probabilities, not text.

- The email must be **spam** with confidence at or above the threshold (85% by default) to be moved. Anything less sure stays
  in the inbox and appears in Review.
- A **keep** is always kept, however sure.
- The email text is treated as data: Jev is told never to follow instructions written inside an email.

## Other workspaces

Make one Spam filter per mailbox or workspace. The starting point differs by workspace, and you can rewrite any box:

- **FSA**: keep only business opportunities (enquiries, quotes, tenders, partnerships, clients); everything else is spam.
- **Media**: keep press enquiries, story and interview requests, tips, and mail from journalists and outlets.
- **Personal**: keep real people, bills and official notices, deliveries you wait for; clear promotions and newsletters.

## Troubleshooting

- **"Add an OpenRouter key first"**: connect OpenRouter under Agents, Settings, Providers.
- **Nothing happens on a run**: the notification bell shows the error; the Run tab shows how many were read, protected and judged.
- **Too much is kept**: lower the threshold on the spam label (Advanced), or sharpen **Spam**.
- **Something important was moved**: open the mailbox's Trash and move it back; then add its sender to **Safe addresses**.

## Teaching it from mistakes

When the agent calls something spam and it isn't:

1. In **Review** or an **execution**, press **Not spam** on the email.
2. Say why, in your own words (a few suggested reasons are one click away). The agent reads its whole setup, the email and your
   reason, and suggests **one extra point**, for example "Mail from partner organisations about joint events is always
   relevant."
3. Edit it if you like and press **Add to the prompt**. You can also tick **Always keep mail from this domain**.

Your five boxes are never changed. The extra points are added after them when the prompt is sent, and are listed under
**Settings, What it has learned**, where you can remove any of them. Choose a chat model for the agent (Settings) so the agent
can write the point; without one, your own words are used as the point.
