birth_ticks() (
    [ -f "$1" ] && [ ! -L "$1" ] || exit 0
    rate=$(getconf CLK_TCK 2>/dev/null) || rate=
    case "$rate" in ''|0*|*[!0-9]*)
        case "$(uname -m)" in arm|armv[4-8]*) ;; *) exit 0;; esac
        rate=$(od -An -v -t u4 /proc/self/auxv | awk '{for(i=1;i<=NF;i++){if(++n%2)t=$i;else if(t==17 && $i>0){print $i;exit}}}')
    ;; esac
    case "$rate" in ''|0*|*[!0-9]*) exit 0;; esac
    awk -v rate="$rate" 'NR==1 && NF==7 && $2~/^[1-9][0-9]*$/ && $6~/^[1-9][0-9]*$/ && $6+0<=$2+0 {worker=$6;app=$2}
        END {if(NR==1 && worker!="")printf "birth_ticks %s %s %s\n",rate,worker,app}' "$1" || :
) 2>/dev/null
