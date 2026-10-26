"""
@file sampleManager.py
@brief Triggers sampling every few meters on sampling legs and logs the samples (FUN-3).
"""
from __future__ import annotations

from typing import Optional

from onboard.onboardConfiguration import OnboardConfiguration
from onboard.onboardGeometry import distanceBetweenPositions
from onboard.onboardTypes import GeographicPosition, SampleRecord


class SampleManager:
    """
    @brief Creates one sample per sample spacing travelled on a sampling leg.
    @details Samples between two ticks are placed by linear interpolation, so a long time step
             does not lose samples. Samples are kept until the reporter takes them.
    @param configuration Onboard configuration.
    """

    def __init__(self, configuration: OnboardConfiguration):
        self.configuration = configuration
        self.distanceSinceLastSampleMeters = 0.0
        self.previousPosition: Optional[GeographicPosition] = None
        self.unsentSamples: list[SampleRecord] = []
        self.totalSampleCount = 0

    def interrupt(self) -> None:
        """
        @brief Forgets the previous position, called while sampling is not running.
        """
        self.previousPosition = None
        self.distanceSinceLastSampleMeters = 0.0

    def update(self, timeSeconds: float, position: GeographicPosition,
               travelledLegIsSampling: bool, payloadReadings: dict[str, float]) -> None:
        """
        @brief Adds the samples due since the previous tick.
        """
        previous = self.previousPosition
        self.previousPosition = position
        if previous is None or not travelledLegIsSampling:
            self.distanceSinceLastSampleMeters = 0.0
            return
        segmentLength = distanceBetweenPositions(previous, position)
        if segmentLength < 1e-9:
            return
        spacing = self.configuration.sampleSpacingMeters
        remainingSegment = segmentLength
        travelledAlongSegment = 0.0
        while self.distanceSinceLastSampleMeters + remainingSegment >= spacing:
            stepLength = spacing - self.distanceSinceLastSampleMeters
            travelledAlongSegment += stepLength
            remainingSegment -= stepLength
            self.distanceSinceLastSampleMeters = 0.0
            fraction = travelledAlongSegment / segmentLength
            samplePosition = GeographicPosition(
                previous.latitude + fraction * (position.latitude - previous.latitude),
                previous.longitude + fraction * (position.longitude - previous.longitude))
            self.unsentSamples.append(SampleRecord(timeSeconds, samplePosition, dict(payloadReadings)))
            self.totalSampleCount += 1
        self.distanceSinceLastSampleMeters += remainingSegment

    def takeUnsentSamples(self) -> list[SampleRecord]:
        """
        @brief Returns the samples not yet reported and clears them.
        """
        samples, self.unsentSamples = self.unsentSamples, []
        return samples
