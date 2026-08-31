# AI DISCLOSURE:
# This code was developed with assistance from OpenAI's ChatGPT.
# AI-generated suggestions were reviewed, modified, and integrated by the author.


from pathlib import Path


BITRATES = {
    # MPEG 1, Layer III
    1: [
        0, 32, 40, 48, 56, 64, 80, 96,
        112, 128, 160, 192, 224, 256, 320, 0
    ],

    # MPEG 2 / 2.5, Layer III
    2: [
        0, 8, 16, 24, 32, 40, 48, 56,
        64, 80, 96, 112, 128, 144, 160, 0
    ],
}


SAMPLE_RATES: dict[int | float, list[int]] = {
    1:   [44100, 48000, 32000],
    2:   [22050, 24000, 16000],
    2.5: [11025, 12000, 8000],
}


def mp3_metadata(path: Path) -> tuple[int, int]:
    """
    I am NOT going to pip install some dependency just for TWO bits of data in 
    an mp3 file. 

    Returns a tuple of integers. 

    index0: sample rate\n
    index1: sample count
    """
    data = path.read_bytes()

    offset = 0

    # Skip an ID3v2 tag, if present.
    if data.startswith(b"ID3") and len(data) >= 10:
        size = (
            ((data[6] & 0x7f) << 21)
            | ((data[7] & 0x7f) << 14)
            | ((data[8] & 0x7f) << 7)
            | (data[9] & 0x7f)
        )

        offset = 10 + size

    sample_rate = None
    sample_count = 0

    while offset + 4 <= len(data):
        header = int.from_bytes(data[offset:offset + 4], "big")

        # 11-bit MPEG frame sync
        if (header >> 21) & 0x7ff != 0x7ff:
            offset += 1
            continue

        version_bits = (header >> 19) & 0b11
        layer_bits = (header >> 17) & 0b11
        bitrate_index = (header >> 12) & 0b1111
        sample_rate_index = (header >> 10) & 0b11
        padding = (header >> 9) & 1

        # Reserved MPEG version
        if version_bits == 0b01:
            offset += 1
            continue

        # We only support Layer III here.
        if layer_bits != 0b01:
            raise ValueError("Only MPEG Audio Layer III (3) is supported.")

        if bitrate_index in (0, 15):
            offset += 1
            continue

        if sample_rate_index == 3:
            offset += 1
            continue

        if version_bits == 0b11:
            version = 1
        elif version_bits == 0b10:
            version = 2
        else:
            version = 2.5

        rate = SAMPLE_RATES[version][sample_rate_index]

        bitrate_table = (
            BITRATES[1]
            if version == 1
            else BITRATES[2]
        )

        bitrate = bitrate_table[bitrate_index] * 1000

        if version == 1:
            frame_size = (
                (144 * bitrate) // rate
                + padding
            )

            samples_per_frame = 1152

        else:
            frame_size = (
                (72 * bitrate) // rate
                + padding
            )

            samples_per_frame = 576

        if sample_rate is None:
            sample_rate = rate
        elif sample_rate != rate:
            raise ValueError("MP3 sample rate changes between frames")

        sample_count += samples_per_frame
        offset += frame_size

    if sample_rate is None:
        raise ValueError("No valid MP3 frames found")

    return sample_rate, sample_count