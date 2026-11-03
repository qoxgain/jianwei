/** Display fixtures only; these values never appear as executed real experiments. */
import {describe,it,expect,vi} from 'vitest';
import {renderToStaticMarkup} from 'react-dom/server';
vi.hoisted(()=>{Object.assign(globalThis,{location:{protocol:'file:',search:''}});});
import {distribution,RecordedPlot} from './RobustnessPanel';

describe('recorded robustness statistics',()=>{
 it('describes seed dispersion without calling it independent confidence',()=>{
  expect(distribution([1,2,3,4])).toEqual({median:2.5,min:1,max:4,std:Math.sqrt(1.25)});
  expect(distribution([])).toBeNull();expect(distribution([2,2,2])?.std).toBe(0);
 });
 it('does not fabricate an empty learning curve',()=>{
  const html=renderToStaticMarkup(<RecordedPlot label="fixture empty" series={[]}/>);
  expect(html).toContain('没有已执行曲线');expect(html).not.toContain('polyline');
 });
 it('uses observed time spacing rather than pretending irregular timestamps are uniform',()=>{
  const html=renderToStaticMarkup(<RecordedPlot label="fixture PTS" times={[1,2,11]} series={[{name:'observed',values:[1,1,1]}]}/>);
  expect(html).toContain('50,');expect(html).toContain('108,');expect(html).toContain('630,');
  expect(html).toContain('fixture PTS');expect(html).not.toMatch(/NaN|Infinity/);
 });
 it('supports comparable raw-space train and validation curves and genuine before/after points',()=>{
  const html=renderToStaticMarkup(<RecordedPlot label="fixture raw MSE" series={[{name:'train',values:[.4,.2]},{name:'validation',values:[.4,.3]}]}/>);
  expect(html.match(/<polyline /g)?.length).toBe(2);expect(html).toContain('train');expect(html).toContain('validation');
 });
});
