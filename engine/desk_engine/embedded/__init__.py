"""Embedded Agent Gateway: lets a partner application run OfinTech Desk's financial agent inside its own UI over
the AG-UI protocol.

Trust model in one paragraph (details in docs/embedded/ARCHITECTURE.md): a workspace admin registers an
*integration*, which creates a dedicated viewer *service user* in that one workspace and a long-lived integration
key for the partner's backend. The partner's backend exchanges the key for short-lived *session tokens*, one per
end user, that the partner's web page sends to the gateway. Workspace, acting user and role always come from the
integration row behind the token — never from anything the client sends. Runs are limited to a read-only tool
scope; approvals, when a future agent needs them, are decided inside Desk by an editor.
"""
