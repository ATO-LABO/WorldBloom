"""Accept only the ordinary single-disk ZIP structure emitted by the packager."""
import io
import stat
import struct
from pathlib import PurePosixPath
import zipfile
import zlib
from package_release import safe_name

ARCHIVE_SUFFIXES = {'.zip', '.tar', '.gz', '.bz2', '.xz', '.7z', '.rar', '.zst',
                    '.whl', '.egg', '.jar', '.pyz', '.pkg'}
ARCHIVE_MAGICS = (b'PK\x03\x04', b'PK\x05\x06', b'PK\x07\x08', b'\x1f\x8b',
                  b'BZh', b'\xfd7zXZ\0', b'7z\xbc\xaf\x27\x1c', b'Rar!\x1a\x07',
                  b'\x28\xb5\x2f\xfd', b'PYZ\0')


def is_archive(name, data):
    return (PurePosixPath(name).suffix.lower() in ARCHIVE_SUFFIXES
            or data.startswith(ARCHIVE_MAGICS) or data[257:262] == b'ustar'
            or zipfile.is_zipfile(io.BytesIO(data)))


def validate_zip(guard, name, data, content_checker):
    def fail(reason):
        guard.reject('unsupported-release-zip-' + reason, name)

    if len(data) > 512 * 1024 * 1024 or len(data) < 22 or data[-22:-18] != b'PK\x05\x06':
        fail('size-comment-or-trailing-data')
    end = struct.unpack_from('<4s4H2IH', data, len(data) - 22)
    _, disk, cd_disk, disk_count, count, cd_size, cd_offset, comment_len = end
    if (disk or cd_disk or disk_count != count or count > 10000 or count == 65535
            or comment_len or cd_offset + cd_size != len(data) - 22):
        fail('multidisk-zip64-or-unparsed-data')
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries = archive.infolist()
        if archive.comment or len(entries) != count or archive.start_dir != cd_offset:
            fail('central-directory')
        cursor, central, total, names = 0, cd_offset, 0, set()
        for entry in entries:
            original = entry.orig_filename
            try:
                safe_name(original)
            except ValueError:
                fail('unsafe-name')
            if original != entry.filename or original.casefold() in names:
                fail('normalized-or-duplicate-name')
            names.add(original.casefold())
            guard.check_name(original)
            # Filenames are textual metadata and are never notice exceptions.
            content_checker('ZIP-entry-name.txt', original.encode('utf-8'))
            if (entry.is_dir() or stat.S_IFMT(entry.external_attr >> 16) not in (0, stat.S_IFREG)
                    or entry.flag_bits & ~0x800 or entry.extra or entry.comment
                    or entry.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                    or entry.extract_version not in (10, 20) or entry.volume != 0):
                fail('flags-method-link-or-extra-metadata')
            total += entry.file_size
            if entry.file_size > 256 * 1024 * 1024 or total > 1024 * 1024 * 1024:
                fail('expanded-size')
            encoded = original.encode('utf-8' if entry.flag_bits & 0x800 else 'cp437')
            if entry.header_offset != cursor or data[cursor:cursor + 4] != b'PK\x03\x04':
                fail('local-offset-or-hidden-data')
            local = struct.unpack_from('<4s5H3I2H', data, cursor)
            _, version, flags, method, mtime, mdate, crc, compressed, size, nlen, xlen = local
            if (version != entry.extract_version or flags != entry.flag_bits or method != entry.compress_type
                    or crc != entry.CRC or compressed != entry.compress_size or size != entry.file_size
                    or xlen or nlen != len(encoded) or data[cursor + 30:cursor + 30 + nlen] != encoded):
                fail('local-header-mismatch')
            payload = data[cursor + 30 + nlen:cursor + 30 + nlen + compressed]
            if method == zipfile.ZIP_STORED:
                if compressed != size:
                    fail('stored-size-or-hidden-payload')
            else:
                inflater = zlib.decompressobj(-15)
                decoded = inflater.decompress(payload, size + 1)
                if (len(decoded) != size or not inflater.eof or inflater.unused_data
                        or inflater.unconsumed_tail):
                    fail('deflate-boundary-or-hidden-payload')
            cursor += 30 + nlen + compressed
            if cursor > cd_offset or data[central:central + 4] != b'PK\x01\x02':
                fail('overlap-or-central-header')
            header = struct.unpack_from('<4s6H3I5H2I', data, central)
            _, made, needed, cflags, cmethod, ctime, cdate, ccrc, csize, usize, fnlen, extra, comment, volume, internal, external, offset = header
            if (made >> 8 not in (0, 3) or made & 255 not in (10, 20)
                    or internal not in (0, 1) or external & 0xffff not in (0, 0x20)
                    or needed != version or cflags != flags or cmethod != method or ctime != mtime or cdate != mdate
                    or ccrc != crc or csize != compressed or usize != size or fnlen != nlen
                    or extra or comment or volume or offset != entry.header_offset
                    or external != entry.external_attr or internal != entry.internal_attr
                    or data[central + 46:central + 46 + fnlen] != encoded):
                fail('central-local-mismatch-or-extra')
            central += 46 + fnlen
        if cursor != cd_offset or central != len(data) - 22:
            fail('unparsed-gap-or-directory-data')
    return count
