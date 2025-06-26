from kubernetes import client, config
from kubernetes.client import NetworkingV1Api
from uptime_kuma_api import MonitorType, UptimeKumaApi
from typing import Any
from dataclasses import dataclass
import os
import time


@dataclass
class Ingress:
    host: str
    ignore: bool
    secure: bool

    def __hash__(self) -> int:
        return hash(f"{self.host}-{self.ignore}-{self.secure}")


def get_networking_api_client() -> NetworkingV1Api:
    try:
        config.load_kube_config()
    except Exception as e:
        print(f"Error loading kube config: {e}")
        print("Using in-cluster config")
        config.load_incluster_config()
    return client.NetworkingV1Api()


def get_uptime_kuma_api_client(url: str, username: str, password: str) -> UptimeKumaApi:
    client = UptimeKumaApi(url)
    client.login(
        username=username,
        password=password,
    )
    return client


def get_or_create_tag(client: UptimeKumaApi, tag: str) -> dict[Any, Any]:
    tags = client.get_tags()
    for t in tags:
        if t["name"] == tag:
            return t
    return client.add_tag(name=tag, color="green")


def main() -> int:
    UPTIME_KUMA_API_URL = os.getenv("UPTIME_KUMA_API_URL")
    CONTROLLER_TAG = os.getenv("CONTROLLER_TAG")
    KUMA_USERNAME = os.getenv("KUMA_USERNAME")
    KUMA_PASSWORD = os.getenv("KUMA_PASSWORD")
    PROD = os.getenv("PROD")
    if (
        not UPTIME_KUMA_API_URL
        or not CONTROLLER_TAG
        or not KUMA_USERNAME
        or not KUMA_PASSWORD
    ):
        print(
            "UPTIME_KUMA_API_URL, CONTROLLER_TAG, KUMA_USERNAME, and KUMA_PASSWORD environment variables are not set"
        )
        if PROD:
            print("Exiting...")
            return 1
        print("Running with development defaults")
        UPTIME_KUMA_API_URL = "http://localhost:3001"
        CONTROLLER_TAG = "k8s-ingress"
        KUMA_USERNAME = "arya"
        KUMA_PASSWORD = "Arrk1174"

    k8s_client = get_networking_api_client()
    uptime_kuma_client = get_uptime_kuma_api_client(
        UPTIME_KUMA_API_URL, KUMA_USERNAME, KUMA_PASSWORD
    )
    tag = get_or_create_tag(uptime_kuma_client, CONTROLLER_TAG)
    monitors = get_monitors(uptime_kuma_client, CONTROLLER_TAG)
    ingress_hosts = get_ingress_hosts(k8s_client)
    prunable_monitors = filter_prunable_monitors(monitors, ingress_hosts)
    print(f"Prunable monitors: {prunable_monitors}")
    missing_monitors = filter_missing_monitors(monitors, ingress_hosts)
    for monitor in prunable_monitors:
        print(f"Pruning monitor: {monitor['url']}")
        try:
            uptime_kuma_client.delete_monitor(monitor["id"])
        except Exception as e:
            print(f"Error deleting monitor: {e}")

    for ingress in missing_monitors:
        print(f"Adding monitor: {ingress.host}")
        monitor_added = uptime_kuma_client.add_monitor(
            type=MonitorType.HTTP,
            url=f"{'https' if ingress.secure else 'http'}://{ingress.host}",
            name=ingress.host,
        )
        uptime_kuma_client.add_monitor_tag(
            tag_id=tag["id"], monitor_id=monitor_added["monitorID"]
        )
    uptime_kuma_client.disconnect()
    return 0


def get_ingress_hosts(client: NetworkingV1Api) -> set[Ingress]:
    ingress = client.list_ingress_for_all_namespaces()
    hosts: set[Ingress] = set()
    for ingressItem in ingress.items:
        ignore = (
            str(ingressItem.metadata.annotations.get("uptime-kuma-controller.ignore"))
            == "true"
        )
        secure = ingressItem.metadata.annotations.get("uptime-kuma-controller.secure")
        if secure is None:
            secure = True
        else:
            secure = str(secure) == "true"
        for rule in ingressItem.spec.rules:
            try:
                hosts.add(
                    i := Ingress(
                        host=rule.host,
                        ignore=ignore,
                        secure=secure,
                    )
                )
                print(i)
            except Exception as e:
                print(f"Error adding host: {e}")

    return hosts


def get_monitors(client: UptimeKumaApi, tag: str) -> list[dict[Any, Any]]:
    monitors = client.get_monitors()
    return filter_monitor_by_tag(monitors, tag)


def strip_url_components(url: str) -> str:
    return url.split("/")[-1].replace("https://", "").replace("http://", "")


def filter_monitor_by_tag(
    monitors: list[dict[Any, Any]], tag: str
) -> list[dict[Any, Any]]:
    filtered_monitors: list[dict[Any, Any]] = []
    for monitor in monitors:
        if not len(monitor["tags"]):
            continue
        for monitorTag in monitor["tags"]:
            if monitorTag["name"] == tag:
                filtered_monitors.append(monitor)
    return filtered_monitors


def filter_prunable_monitors(
    monitors: list[dict[Any, Any]], ingress_hosts: set[Ingress]
) -> list[dict[Any, Any]]:
    prunable_monitors: list[dict[Any, Any]] = []
    ingress_hosts_str_arr = [host.host for host in ingress_hosts]
    ignored = [host.host for host in ingress_hosts if host.ignore]
    for monitor in monitors:
        host = strip_url_components(monitor["url"])
        if host not in ingress_hosts_str_arr:
            prunable_monitors.append(monitor)
        elif host in ignored:
            prunable_monitors.append(monitor)
    return prunable_monitors


def filter_missing_monitors(
    monitors: list[dict[Any, Any]], ingress_hosts: set[Ingress]
) -> list[Ingress]:
    missing_monitors: list[Ingress] = []
    monitors_url_list: list[str] = [
        strip_url_components(monitor["url"]) for monitor in monitors
    ]
    for ingress in ingress_hosts:
        if ingress.host not in monitors_url_list and not ingress.ignore:
            missing_monitors.append(ingress)
    return missing_monitors


if __name__ == "__main__":
    while True:
        print("buffer... starting in 10 seconds")
        time.sleep(10)
        main()
        print("done. starting again in 30 seconds")
