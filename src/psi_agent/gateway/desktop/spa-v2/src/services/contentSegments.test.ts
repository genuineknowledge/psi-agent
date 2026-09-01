import { describe, expect, it } from 'vitest'
import {
  appendContentSegment,
  contentSegmentsStart,
  sealContentBeforeTools,
  settleContentSegments,
  streamSegmentBodies,
} from './contentSegments'

describe('contentSegments', () => {
  it('accumulates sealed notes as interim and streams the next segment as text', () => {
    let seg = contentSegmentsStart()
    seg = appendContentSegment(seg, 'step-one')
    expect(streamSegmentBodies(seg)).toEqual({ interimText: '', text: 'step-one' })

    seg = sealContentBeforeTools(seg)
    expect(streamSegmentBodies(seg)).toEqual({ interimText: 'step-one', text: '' })

    seg = appendContentSegment(seg, 'step-two')
    expect(streamSegmentBodies(seg)).toEqual({
      interimText: 'step-one',
      text: 'step-two',
    })

    seg = sealContentBeforeTools(seg)
    seg = appendContentSegment(seg, 'final-summary')
    expect(streamSegmentBodies(seg)).toEqual({
      interimText: 'step-one\n\nstep-two',
      text: 'final-summary',
    })
    expect(settleContentSegments(seg)).toEqual({
      finalText: 'final-summary',
    })
  })

  it('promotes last sealed note when there is no trailing summary', () => {
    let seg = contentSegmentsStart()
    seg = appendContentSegment(seg, 'only-step')
    seg = sealContentBeforeTools(seg)
    expect(settleContentSegments(seg)).toEqual({
      finalText: 'only-step',
    })
  })

  it('ignores blank seals', () => {
    let seg = contentSegmentsStart()
    seg = sealContentBeforeTools(seg)
    seg = appendContentSegment(seg, '  final  ')
    expect(settleContentSegments(seg)).toEqual({
      finalText: 'final',
    })
  })
})
