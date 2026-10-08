// Queue page: pending decompilation jobs, overall and per decompiler.

let refreshSchedule = -1;

function updateQueue() {
    if (refreshSchedule !== -1) {
        clearTimeout(refreshSchedule);
    }

    fetch(Fusion.API + 'queue')
        .then(resp => {
            if (resp.ok) {
                return resp.json();
            } else {
                throw Error("Error loading queue");
            }
        })
        .then(data => {
            setQueue(data);
        })
        .catch(() => {})
        .finally(() => {
            refreshSchedule = setTimeout(updateQueue, 5000);
        });
}

updateQueue();


function queueCard(title, subtitle, info, extraClass) {
    let card = document.createElement("div");
    card.className = "queue-card" + (extraClass ? " " + extraClass : "");

    let header = document.createElement("h2");
    header.textContent = title;
    if (subtitle) {
        let small = document.createElement("small");
        small.textContent = " " + subtitle;
        header.append(small);
    }

    let count = document.createElement("p");
    count.className = "count";
    count.textContent = info.queue_length === 0 ? "Empty" : info.queue_length + " pending";

    let detail = document.createElement("p");
    if (info.queue_length !== 0 && info.oldest_unfinished) {
        detail.textContent = "Oldest job: " + new Date(info.oldest_unfinished).toLocaleString();
    }

    card.append(header, count, detail);
    return card;
}


function setQueue(data) {
    let queueDiv = document.getElementById("queue");

    let decomps = Object.values(data.per_decompiler).sort((a, b) =>
        a.decompiler.name.toLowerCase().localeCompare(b.decompiler.name.toLowerCase()));

    queueDiv.replaceChildren(
        queueCard("All decompilers", "", data.general, "queue-card-overall"),
        ...decomps.map(q => {
            let revision = q.decompiler.revision ? ` (${q.decompiler.revision.substring(0, 8)})` : "";
            let version = q.decompiler.version + revision;
            return queueCard(q.decompiler.name, version, q);
        })
    );
}
