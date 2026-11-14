# Linux / VM reference deployment

Production recommendation: **explicit internal gateway endpoint** plus **host firewall deny** for catalog provider destinations.

## Components

| File | Purpose |
|------|---------|
| `rai-gateway.service` | systemd unit for gateway on the VM |
| `docker-compose.gateway-host.yml` | single-host Docker deployment |
| `nftables/rai-egress-observe.nft` | No blocks; use in OBSERVE |
| `nftables/rai-egress-enforce.nft` | Drop outbound 443 for `rai-workload` group except gateway IP |

```bash
sudo nft -f deploy/network-enforcement/linux/nftables/rai-egress-enforce.nft
sudo systemctl enable --now rai-gateway.service
```

Workloads run as system user in group `rai-workload` without provider API keys in environment.