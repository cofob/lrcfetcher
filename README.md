# lrcfetcher

A tool to automatically embed synchronized lyrics into your FLAC files. It supports both local `.lrc` files and an online API ([LRCLIB.net](https://lrclib.net/)).

## Installation

Install this package locally:

```sh
pip install lrcfetcher
```

## Usage

```sh
lrcfetcher [DIRECTORY] [OPTIONS]
```

- `DIRECTORY` (optional, defaults to current directory)
- `-f, --force`: Overwrite existing lyrics if present
- `--only-local`: Use only local `.lrc` files
- `--only-online`: Always fetch lyrics from an API
- `--local-folder PATH`: Specify a folder containing `.lrc` files

## Example

```sh
lrcfetcher .
```

This will search for `.flac` files in the current directory, skip any that already have lyrics.
