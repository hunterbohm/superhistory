# Sync

Sync keeps the knowledge bases on your computers in step. Something read on one computer, a session, a note or a
commit, shows up in search on the others. It goes through a relay that stores it locked: the relay and Superhistory
cannot read what you sync. Sync is the paid plan, Superhistory Sync. Everything else in Superhistory is free and
works without it.

## What you need

- Superhistory installed on each computer.
- `age` and `ssh-keygen` on each computer. Sync uses `age` to lock what leaves a computer and `ssh-keygen` to sign it.
  - Mac: `ssh-keygen` comes with macOS; install age with `brew install age`.
  - Debian or Ubuntu: `sudo apt install age openssh-client`.
  - `superhistory sync doctor` checks both.
- A Superhistory account with the Sync plan, at https://app.superhistory.dev.

## Your first computer

1. Sign this computer in:

   ```
   superhistory sync login
   ```

   It prints an address and a code. Open https://app.superhistory.dev/device, signed in to your account, type the code
   and approve. Type it yourself: the page never takes a code from a link. Approve only a code your own terminal
   printed.

2. Start Sync from this computer:

   ```
   superhistory sync init
   ```

   It asks you to approve once more in the browser, then prints your recovery code. Save the recovery code in your
   password manager. Without it and without your computers, nobody can get your synced data back, including us.

   It then sends what this computer has read, and adds a `[sync]` table to your config, so every
   `superhistory ingest`, and the scheduled one, syncs after it reads.

## Each next computer

1. Install Superhistory and sign in with `superhistory sync login`, as above.
2. On a computer that already syncs, run:

   ```
   superhistory sync add-device
   ```

   It prints a pairing code. The code works once, for 10 minutes.
3. On the new computer, run:

   ```
   superhistory sync join
   ```

   and type the pairing code. Both computers show words. Check that they match, then answer `y` on the first one.
   The new computer then receives everything synced so far, and its search covers all your computers.

## Every day

- Every `superhistory ingest` syncs after it reads, and so does the scheduled ingest.
- `superhistory sync` syncs now.
- `superhistory sync status` lists your computers, how far this one is behind, what is waiting to go, and the online
  accounts this computer reads, by source name.

## One computer reads each online account

Gmail and GitHub are read from an online account, not from a computer. If two computers read the same account, every
thread would be stored twice. So one computer reads each online account, and Sync refuses a second one:

- At `superhistory sync join`, once you have compared the words, the computer you joined from checks the new
  computer's online accounts. If another computer already reads one of them, the new computer is not added, and both
  say which source and which computer.
- On every sync, a computer that already syncs checks its own config. If you add a source there for an account another
  computer reads, that computer stops syncing and sends nothing until you remove the source. Your other computers keep
  syncing. `superhistory sync status` and `superhistory stats` show it too.

The message names your source and the other computer, never the account:

```
This computer reads the online account in [sources.gmail], which "laptop" already reads in your sync group. One
computer reads each online account. Remove [sources.gmail] from this computer's config, then run `superhistory sync`
again.
```

Sync compares the accounts inside its locked list of your computers, so the relay never sees which accounts you read.

To move an account to another computer, run `superhistory forget NAME` on the computer that reads it now and remove
`[sources.NAME]` from its config, sync it, then add the source on the other computer.

## Removing a computer

- `superhistory sync remove LABEL` on another of your computers, then approve it in the browser. Or remove it on the
  Account page of the website.
- It says how many items that computer read, and asks before it deletes them from every computer. `--keep-items`
  keeps them.
- A removed computer can no longer reach the relay and cannot read anything synced after. It keeps what it already
  had.
- A removed computer also knew your recovery code, so you are offered a new one. `superhistory sync recovery-code
  --new` makes one at any time.

## Getting it back

If you lose every computer, install Superhistory on a new one, sign in with `superhistory sync login`, and run
`superhistory sync recover`. It asks for the recovery code and rebuilds everything from the relay.

## What leaves your computer

- Each item's text, title, time and where it came from, its summaries, and the marks that say one item replaces
  another. Secrets that Superhistory's masking finds are masked before they leave, as they are when it stores them.
- Masking is known to miss two shapes, and Sync sends them as stored. One is a recovery code with a second code
  glued onto its end: the last two groups of the second code get through. The other is a password typed through a
  browser tool, in an item stored before Superhistory's readers learned to hide what a browser tool types, and not read
  again since.
  `tests/sync/test_egress.py` keeps both as expected failures, so the suite fails the day masking catches one and this
  list can lose it.
- Everything is locked with keys only your computers hold, then signed, before it reaches the relay. The relay sees
  how many computers you have and their names, and when and how much they sync. It sees nothing about what you sync.
- The website holds your account, your plan, and each computer's name and public keys.

## Limits

The relay keeps up to 6 GiB for an account and takes up to 1 GiB of changes a day. Past either, nothing is lost:
your changes wait on your computer and go up when there is room. `superhistory sync compact`, run on a computer
where `status` shows nothing waiting, makes room.

## Your own relay

If you run the app and the relay yourself, sign in with `superhistory sync login --app URL --relay URL`.
