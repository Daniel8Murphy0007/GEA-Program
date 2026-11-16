# 29. An assistant that reads the site
Who: office
Where: #/mcp
Takes: 20 minutes
> Letting an assistant read the site's pages and answer questions from them - what it can see, what it cannot do, and the record of every question.

## What it is
The program can serve its pages to an assistant over the Model Context Protocol, which Claude Desktop and other assistants speak. The assistant starts this program as its own child process on this computer: no port is opened and nothing listens on the network. It can only read - it cannot declare, attach, remove or write. What a page returns becomes part of the conversation, and so goes to whoever makes the assistant; choose what it may read with that in mind.

## Steps
1. Open **Assistant access**. **The tools** lists every page an assistant may read - the site, each engineering page, the help - one row each.
2. Untick any page that must not leave the site and press **Keep** (the operator role), with who decided and on what grounds. A tool not offered is not listed to the assistant and cannot be called.
3. For Claude Desktop: an administrator presses **Add to Claude Desktop**. One entry, `gea`, is written into Claude Desktop's own configuration file; every other entry is kept, and a dated copy of the file as it was is left beside it. Quit Claude Desktop completely and start it again; the tools are then under its connectors.
4. For any other assistant: **Copy** the entry shown and paste it into that assistant's configuration.
5. Ask it something a page answers - *which well on the pad had the slowest connections?* - and set its answer against the Rig time tab. The figures are the same, because it is the same function.
6. **The access log** on the tab shows every call: which tool, for which well, when. **Take it out of Claude Desktop** removes the entry the same way it was added.

## You know it worked when
The assistant lists the site's tools, one answer of its agrees with the tab it came from, and the access log shows the call.

## The same from a terminal
`gea mcp --workspace C:\site --status` prints the tools offered and the last calls; `gea mcp --workspace C:\site --call rig_time` prints one tool's answer as an assistant would get it.

Help: mcp
