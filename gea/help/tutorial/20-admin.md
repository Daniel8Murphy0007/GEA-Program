# 20. Users, roles, company sign-in and HTTPS
Who: office
Where: #/admin
Takes: 30 minutes
> Who can reach the control panel, with what, and over what.

## Steps
1. **Administration**, **Users**: an account per person with one role (lesson 2). Five failed sign-ins lock the name and the address for 15 minutes. **Live sessions** lists who is signed in from where and revokes any; a restart signs everyone out.
2. **Company sign-in**: people sign in with the account the company already manages - Microsoft Entra ID, Okta, Google Workspace, Ping, Keycloak or AD FS - so the company's multi-factor, conditional access and leavers apply. Enter the issuer, the client id and secret, the redirect URI, the role claim and the map from the company's app roles to GEA's four roles; **Test the provider**, then **Save**. The sign-in page then shows the company's button above the local sign-in; local accounts stay for the first administrator and for when the company cannot be reached.
3. Reaching it from other computers: the service listens on this computer only until an administrator starts it with `--host 0.0.0.0` or the kit's start-dashboard for the site network. Then **HTTPS**: use the site's certificate (the company's certificate authority, which managed computers already trust) or **Make a self-signed certificate** with the names the panel is reached by; **Download the certificate** for the site's computers. A change takes effect at the next **Restart** under Operations control. A certificate in its last 30 days raises an alert.
4. **Site settings**: the site's name as reports print it, the units and time zone defaults.
5. **File roots**: the import root the program may read and the export root it may write (lesson 4). Nothing outside them is ever browsed.
6. **Workspaces**: a second site on this computer, each its own folder and window.
7. Everything on this page is an audit line with who did it.
8. **Assistant access**: to let an assistant such as Claude Desktop read the site, an administrator presses **Add to Claude Desktop** (then quits Claude Desktop and starts it again); for another MCP client, **Copy** the entry shown into that client's configuration. The assistant starts this program as its own child process - no port is opened - and can only read pages; it cannot declare, attach, remove or write. Untick any page that must not leave the site: what a tool returns goes to the assistant's maker as part of the conversation. Every call is in the access log on the same tab.

## You know it worked when
A colleague signs in from their own PC over https with the company's button, and lands with the role the company's group gives.

Help: site, company-sign-in, https, upkeep, mcp
