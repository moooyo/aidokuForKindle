readback_receipt() { printf 'AB_READBACK_%s %s %s %s\n' "$1" "$kind" "$label" "$expected_boot"; }

readback_result() {
    if [ "$1" = yes ]; then
        readback_receipt CLOSED
    elif absent "$state/armed"; then
        readback_receipt UNTOUCHED
    else
        readback_receipt EXACT
    fi
}

readback_owned() (
    close_scope || exit 1
    if close_owned readback; then
        exit 0
    fi
    # PENDING is not restoration proof. It only allows the host to request
    # its one repair when neither the input nor the close record exists.
    # Known or ambiguous intent does not authorize another repair request.
    close_scope && absent "$state/closed" && absent "$state/input-restore-intent" && readback_receipt PENDING
)
