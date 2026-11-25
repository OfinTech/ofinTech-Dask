# OfinTech Desk Helm chart

The Team Server on Kubernetes: one pod, one volume, one Service, and an Ingress if you ask for one.

**Status.** The chart is linted and rendered in CI on every change (`.github/workflows/helm.yml`). It has **not yet
been run on a cluster by the maintainers**: in particular the read-only root filesystem it sets is stricter than
the Docker Compose install and has not been exercised. Try it on a test cluster first, and tell us what you find.
The Docker Compose install in [docs/DEPLOYMENT.md](../../../docs/DEPLOYMENT.md) is the one that is run every day.

## Install

```sh
helm install desk deploy/helm/ofintech-desk -n ofintech-desk --create-namespace \
  --set publicUrl=https://desk.example.com
```

That gives a Service inside the cluster. `publicUrl` is the address people will type; it must be `https://` and a
host only.

**One replica, always.** The database is a file on the pod's volume, so the chart runs one pod and replaces it on
an upgrade (`Recreate`). Do not scale the Deployment: two pods on one database file corrupt it.

## What the proxy in front must allow

Whatever brings traffic to the Service (an Ingress controller, a Gateway, a load balancer) has to allow two things.
The Docker Compose install sets both in its `Caddyfile`; on Kubernetes they are yours to set.

| | Why | What breaks without it |
|---|---|---|
| Request bodies up to 260 MB | Uploads: a ledger, a bank statement, up to 20 attachments on one message. The Desk checks every body against its own limits (25 MB for one file unless `DESK_MAX_UPLOAD_MB` says otherwise) | nginx-based proxies stop a body at 1 MB by default: importing a file fails with `413` |
| Replies passed on as they arrive | The assistant's answers and its progress are streamed | The answer appears all at once at the end, or the page seems to hang |

An idle stream sends a line every 15 seconds, so an ordinary 60-second read timeout does not cut it.

With the chart's own Ingress, the settings go in `ingress.annotations`, under the names your controller uses.
[`ci/ingress-values.yaml`](ci/ingress-values.yaml) is an example for ingress-nginx, and CI renders it:

```sh
helm install desk deploy/helm/ofintech-desk -n ofintech-desk --create-namespace \
  -f deploy/helm/ofintech-desk/ci/ingress-values.yaml --set publicUrl=https://desk.example.com
```

Two things to know about that example:

- **ingress-nginx was retired by the Kubernetes project in March 2026** and gets no more releases or security
  fixes ([announcement](https://kubernetes.io/blog/2025/11/11/ingress-nginx-retirement/)). The example is for
  clusters that still run it. On another controller, or on Gateway API, set the same two things by its own names;
  for F5's NGINX Ingress Controller the body size is `nginx.org/client-max-body-size`.
- The annotations were checked against the controllers' documentation, not against a running cluster.

The TLS secret (`ingress.tlsSecretName`) has to exist in the namespace; the chart does not create certificates.

## Master key

The chart generates it once and keeps it across upgrades.
Back it up:

```sh
kubectl -n ofintech-desk get secret desk-secret -o jsonpath='{.data.master-key}' | base64 -d
```

**With `helm template`, Argo CD or Flux, set `masterKey.existingSecret`**: the chart cannot read the cluster there
and would make a new key on every sync.

```sh
kubectl -n ofintech-desk create secret generic desk-master-key \
  --from-literal=master-key="$(docker run --rm ofintech0/ofintech-desk:1.5.0 python -m desk_engine keygen)"
# then: --set masterKey.existingSecret=desk-master-key
```

`helm uninstall` keeps the data volume and the generated key.

## Settings

See `values.yaml`. Other `DESK_*` settings, such as mail (`DESK_SMTP_*`) and S3 storage (`DESK_S3_*`),
are listed in [docs/DEPLOYMENT.md](../../../docs/DEPLOYMENT.md) and go in `extraEnv`:

```yaml
extraEnv:
  - name: DESK_SMTP_HOST
    value: smtp.example.com
  - name: DESK_SMTP_PASSWORD
    valueFrom:
      secretKeyRef: { name: desk-mail, key: password }
```