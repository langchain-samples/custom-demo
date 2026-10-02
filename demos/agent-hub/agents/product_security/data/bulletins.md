# Contoso block storage security bulletins

## CSB-2026-0412: Management API authentication bypass (CVE-2026-31842)

- Severity: Critical (CVSS 9.1)
- Affected: 6.1.1.100, 6.1.1.200
- Fixed in: 6.1.2.100 and later
- Summary: a crafted request to the management REST API can skip session validation on
  one endpoint, letting an unauthenticated user on the management network read volume
  configuration.
- Mitigation until upgraded: restrict the management interface to the admin VLAN and
  enable the management ACL (`mgmt acl --enable`).

## CSB-2026-0455: NVMe-oF TCP denial of service (CVE-2026-33107)

- Severity: High (CVSS 7.5)
- Affected: 6.1.2.100
- Fixed in: 6.1.2.200
- Summary: a malformed NVMe-oF/TCP connect from a host on the storage network can crash
  the target service, dropping NVMe-oF sessions for about 90 seconds.
- Mitigation until upgraded: allow NVMe-oF/TCP only from known host subnets.

## CSB-2026-0503: OpenSSL update (CVE-2026-2177)

- Severity: Medium (CVSS 5.9)
- Affected: 6.1.1.100 through 6.1.2.200
- Fixed in: 6.1.3.100 (scheduled November 2026)
- Summary: a timing side channel in the bundled OpenSSL affects the Flex telemetry
  connection. Exploitation requires a network position between the array and Flex.
- Mitigation: none required for most customers; route telemetry through the Contoso
  proxy if the array's path to the internet is untrusted.
